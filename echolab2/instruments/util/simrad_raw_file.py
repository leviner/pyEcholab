# coding=utf-8

#     National Oceanic and Atmospheric Administration (NOAA)
#     Alaskan Fisheries Science Center (AFSC)
#     Resource Assessment and Conservation Engineering (RACE)
#     Midwater Assessment and Conservation Engineering (MACE)

#  THIS SOFTWARE AND ITS DOCUMENTATION ARE CONSIDERED TO BE IN THE PUBLIC DOMAIN
#  AND THUS ARE AVAILABLE FOR UNRESTRICTED PUBLIC USE. THEY ARE FURNISHED "AS IS."
#  THE AUTHORS, THE UNITED STATES GOVERNMENT, ITS INSTRUMENTALITIES, OFFICERS,
#  EMPLOYEES, AND AGENTS MAKE NO WARRANTY, EXPRESS OR IMPLIED, AS TO THE USEFULNESS
#  OF THE SOFTWARE AND DOCUMENTATION FOR ANY PURPOSE. THEY ASSUME NO RESPONSIBILITY
#  (1) FOR THE USE OF THE SOFTWARE AND DOCUMENTATION; OR (2) TO PROVIDE TECHNICAL
#  SUPPORT TO USERS.

'''
.. module:: echolab2.instruments.util.simrad_raw_file

    :synopsis:  A low-level interface for SIMRAD ".raw" formatted files

    Provides the RawSimradFile class, a low-level object for
        interacting with SIMRAD RAW formated datafiles.

| Developed by:  Zac Berkowitz <zac.berkowitz@gmail.com> under contract for
| National Oceanic and Atmospheric Administration (NOAA)
| Alaska Fisheries Science Center (AFSC)
| Midwater Assesment and Conservation Engineering Group (MACE)
|
| Authors:
|       Zac Berkowitz <zac.berkowitz@gmail.com>
|       Rick Towler   <rick.towler@noaa.gov>
| Maintained by:
|       Rick Towler   <rick.towler@noaa.gov>

$Id$



Simrad .raw datagram format

|  size  4 bytes   |             header  12 bytes                 |         data            | size check 4 bytes |
|------------------|----------------------------------------------|-------------------------|--------------------|
|    dgram size    |     dgram type      |      dgram time        |       dgram payload     |     dgram size     |
|   4b as int32    |    4b as string     |   8b as two uint32     |  dgram size - 12 bytes  |    4b as int32     |
| dgram total size | bytes 1-3 are type  | bytes 1-4 NT Time low  |     content varies      | should match first |
| header + payload | byte 4 is version   | bytes 5-8 NT time high |                         |    first size      |
|------------------|---------------------|------------------------|-------------------------|--------------------|


'''

from io import BufferedReader, FileIO, SEEK_SET, SEEK_CUR, SEEK_END
import datetime
import struct
import logging
import re
from . import simrad_parsers

__all__ = ['RawSimradFile']

log = logging.getLogger(__name__)

UTC_NT_EPOCH = datetime.datetime(1601, 1, 1, 0, 0, 0)#, tzinfo=pytz_utc)

class SimradEOF(Exception):

    def __init__(self, message='EOF Reached!'):
        self.message = message


    def __str__(self):
        return self.message


class DatagramSizeError(Exception):

    def __init__(self, message, expected_size_tuple, file_pos=(None, None)):
        self.message = message
        self.expected_size = expected_size_tuple[0]
        self.retrieved_size = expected_size_tuple[1]
        self.file_pos_bytes = file_pos[0]
        self.file_pos_dgrams = file_pos[1]


    def __str__(self):
        errstr = self.message + '%s != %s @ (%s, %s)' % (self.expected_size, self.retrieved_size,
            self.file_pos_bytes, self.file_pos_dgrams)
        return errstr


class DatagramReadError(Exception):

    def __init__(self, message, expected_size_tuple, file_pos=(None, None)):
        self.message = message
        self.expected_size = expected_size_tuple[0]
        self.retrieved_size = expected_size_tuple[1]
        self.file_pos_bytes = file_pos[0]
        self.file_pos_dgrams = file_pos[1]


    def __str__(self):
        errstr = [self.message]
        if self.expected_size is not None:
            errstr.append('%s != %s' % (self.expected_size, self.retrieved_size))
        if self.file_pos_bytes is not None:
            errstr.append('@ (%sL, %s)' % (self.file_pos_bytes, self.file_pos_dgrams))

        return ' '.join(errstr)


class RawSimradFile(BufferedReader):
    '''
    A low-level extension of the built in python file object allowing the reading/writing
    of SIMRAD RAW files on datagram by datagram basis (instead of at the byte level.)

    Calls to the read method return parse datagrams as dicts.
    '''
    #: Dict object with datagram header/python class key/value pairs
    DGRAM_TYPE_KEY = {'RAW': simrad_parsers.SimradRawParser(),
                      'CON': simrad_parsers.SimradConfigParser(),
                      'TAG': simrad_parsers.SimradAnnotationParser(),
                      'NME': simrad_parsers.SimradNMEAParser(),
                      'BOT': simrad_parsers.SimradBottomParser(),
                      'DEP': simrad_parsers.SimradDepthParser(),
                      'XML': simrad_parsers.SimradXMLParser(),
                      'FIL': simrad_parsers.SimradFILParser(),
                      'MRU': simrad_parsers.SimradMRUParser(),
                      'IDX': simrad_parsers.SimradIDXParser(),
                      }

    def __init__(self, name, mode='rb', closefd=True, return_raw=False, buffer_size=1024*1024):

        #  9-28-18 RHT: Changed RawSimradFile to implement BufferedReader instead of
        #  io.FileIO to increase performance.

        #  create a raw file object for the buffered reader
        fio = FileIO(name, mode=mode, closefd=closefd)

        #  initialize the superclass
        BufferedReader.__init__(self, fio, buffer_size=buffer_size)
        self._current_dgram_offset = 0
        self._total_dgram_count = None
        self._return_raw = return_raw


    def _seek_bytes(self, bytes_, whence=0):
        '''
        :param bytes_: byte offset
        :type bytes_: int

        :param whence:

        Seeks a file by bytes instead of datagrams.
        '''

        BufferedReader.seek(self, bytes_, whence)


    def _tell_bytes(self):
        '''
        Returns the file pointer position in bytes.
        '''

        return BufferedReader.tell(self)


    def _read_dgram_size(self):
        '''
        Attempts to read the size of the next datagram in the file.
        '''

        buf = self._read_bytes(4)
        if len(buf) != 4:
            self._seek_bytes(-len(buf), SEEK_CUR)
            raise DatagramReadError('Short read while getting dgram size', (4, len(buf)),
                file_pos=(self._tell_bytes(), self.tell()))
        else:
            return struct.unpack('=l', buf)[0] #This return value is an int object.


    def _bytes_remaining(self):
        old_pos = self._tell_bytes()
        self._seek_bytes(0, SEEK_END)
        end_pos = self._tell_bytes()
        offset = end_pos - old_pos
        self._seek_bytes(old_pos, SEEK_SET)

        return offset


    def _read_timestamp(self):
        '''
        Attempts to read the datagram timestamp.
        '''

        buf = self._read_bytes(8)
        if len(buf) != 8:
            if self.at_eof():
                raise SimradEOF()
            else:
                self._seek_bytes(-len(buf), SEEK_CUR)
                raise DatagramReadError('Short read while getting timestamp',
                    (8, len(buf)), file_pos=(self._tell_bytes(), self.tell()))

        else:
            lowDateField, highDateField = struct.unpack('=2L', buf)
            #  11/26/19 - RHT - modified to return the raw bytes
            return lowDateField, highDateField, buf


    def _read_dgram_header(self):
        '''
        :returns: dgram_size, dgram_type, (low_date, high_date)

        Attempts to read the datagram header consisting of:

            long        dgram_size
            char[4]     type
            long        lowDateField
            long        highDateField
        '''

        try:
            dgram_size = self._read_dgram_size()
        except Exception:
            if self.at_eof():
                raise SimradEOF()
            else:
                raise

        #  get the datagram type
        buf = self._read_bytes(4)

        if len(buf) != 4:
            if self.at_eof():
                raise SimradEOF()
            else:
                self._seek_bytes(-len(buf), SEEK_CUR)
                raise DatagramReadError('Short read while getting dgram type', (4, len(buf)),
                    file_pos=(self._tell_bytes(), self.tell()))
        else:
            dgram_type = buf
        dgram_type = dgram_type.decode('iso-8859-1')

        #  11/26/19 - RHT
        #  As part of the rewrite of read to remove the reverse seeking,
        #  store the raw header bytes so we can prepend them to the raw
        #  data bytes and pass it all to the parser.
        raw_bytes = buf

        #  read the timestamp - this method was also modified to return
        #  the raw bytes
        lowDateField, highDateField, buf = self._read_timestamp()

        #  add the timestamp bytes to the raw_bytes string
        raw_bytes += buf

        #  set the total bytes of this datagram including header and trailing size
        bytes_read = dgram_size + 20

        return dict(size=dgram_size, type=dgram_type, low_date=lowDateField,
                high_date=highDateField, raw_bytes=raw_bytes, bytes_read=bytes_read)


    def _read_bytes(self, k):
        '''
        Reads raw bytes from the file
        '''

        return BufferedReader.read(self, k)


    def _read_next_dgram(self, header=None):
        '''
        Attempts to read the next datagram from the file.

        Returns the datagram as a raw string
        '''

        #  11/26/19 - RHT - Modified this method so it doesn't "peek"
        #  at the next datagram before reading which was inefficient.
        #  To minimize changes to the code, methods to read the header
        #  and timestamp were modified to return the raw bytes which
        #  allows us to pass them onto the parser without having to
        #  rewind and read again as was previously done.

        #  try to read the header of the next datagram
        if header is None:
            #  store our current location in the file
            old_file_pos = self._tell_bytes()

            try:
                #  read the datagram header
                header = self._read_dgram_header()
            except DatagramReadError as e:
                e.message = 'Short read while getting raw file datagram header'
                raise e

        else:
            #  we've already read the header so subtract 16 bytes from the
            #  current position.
            old_file_pos = self._tell_bytes() - 16

        #  basic sanity check on size
        if header['size'] < 16:
            #  size can't be smaller than the header size
            log.warning('Invalid datagram header: size: %d, type: %s, nt_date: %s.  dgram_size < 16',
                header['size'], header['type'], str((header['low_date'], header['high_date'])))

            #  see if we can find the next datagram
            self._find_next_datagram()

            #  and then return that
            return self._read_next_dgram()

        #  get the raw bytes from the header
        raw_dgram = header['raw_bytes']

        #  and append the rest of the datagram - we subtract 12
        #  since we have already read 12 bytes: 4 for type and
        #  8 for time.
        raw_dgram += self._read_bytes(header['size'] - 12)

        #  determine the size of the payload in bytes
        bytes_read = len(raw_dgram)

        #  and make sure it checks out
        if bytes_read < header['size']:
            log.warning('Datagram %d (@%d) shorter than expected length:  %d < %d', self.tell(),
                        old_file_pos, bytes_read, header['size'])
            self._find_next_datagram()
            return self._read_next_dgram()

        #  now read the trailing size value
        try:
            dgram_size_check = self._read_dgram_size()
        except DatagramReadError as e:
            self._seek_bytes(old_file_pos, SEEK_SET)
            e.message = 'Short read while getting trailing raw file datagram size for check'
            raise e

        #  make sure they match
        if header['size'] != dgram_size_check:
            log.warning('Datagram failed size check:  %d != %d @ (%d, %d)',
                header['size'], dgram_size_check, self._tell_bytes(), self.tell())
            self._find_next_datagram()

            return self._read_next_dgram()

        #  add the header (16 bytes) and repeated size (4 bytes) to the payload
        #  bytes to get the total bytes read for this datagram.
        bytes_read = bytes_read + 20

        if self._return_raw:
            self._current_dgram_offset += 1
            return raw_dgram
        else:
            nice_dgram = self._convert_raw_datagram(raw_dgram, bytes_read)
            self._current_dgram_offset += 1
            return nice_dgram


    def _convert_raw_datagram(self, raw_datagram_string, bytes_read):
        '''
        :param raw_datagram_string: bytestring containing datagram (first 4
            bytes indicate datagram type, such as 'RAW0')
        :type raw_datagram_string: str

        :param bytes_read: integer specifying the datagram size, including header
            in bytes,
        :type bytes_read: int

        Returns a formatted datagram object using the data in raw_datagram_string
        '''

        #  11/26/19 - RHT - Modified this method to pass through the number of
        #  bytes read so we can bubble that up to the user.

        #  07/17/22 - RHT - Modified to partially parse unknown datagram types

        dgram_type = raw_datagram_string[:3].decode('iso-8859-1')
        try:
            parser = self.DGRAM_TYPE_KEY[dgram_type]
            nice_dgram = parser.from_string(raw_datagram_string, bytes_read)
        except KeyError:
            #  Unknown datagram type

            parser = simrad_parsers.SimradUnknownParser(dgram_type)
            nice_dgram = parser.from_string(raw_datagram_string, bytes_read)

        return nice_dgram


    def _set_total_dgram_count(self):
        '''
        Skips quickly through the file counting datagrams and stores the
        resulting number in self._total_dgram_count

        :raises: ValueError if self._total_dgram_count is not None (it has been set before)
        '''
        if self._total_dgram_count is not None:
            raise ValueError('self._total_dgram_count has already been set. ' +
                    'Call .reset() first if you really want to recount')

        #Save current position for later
        old_file_pos = self._tell_bytes()
        old_dgram_offset = self.tell()

        self._current_dgram_offset = 0
        self._seek_bytes(0, SEEK_SET)

        while True:
            try:
                self.skip()
            except (DatagramReadError, SimradEOF):
                self._total_dgram_count = self.tell()
                break

        #Return to where we started
        self._seek_bytes(old_file_pos, SEEK_SET)
        self._current_dgram_offset = old_dgram_offset


    def at_eof(self):
        old_pos = self._tell_bytes()
        self._seek_bytes(0, SEEK_END)
        eof_pos = self._tell_bytes()

        #Check to see if we're at the end of file and raise EOF
        if old_pos == eof_pos:
            return True

        #Otherwise, go back to where we were and re-raise the original
        #exception
        else:
            offset = old_pos - eof_pos
            self._seek_bytes(offset, SEEK_END)
            return False


    def read(self, k, header=None):
        '''
        :param k: Number of datagrams to read
        :type k: int

        Reads the next k datagrams.  A list of datagrams is returned if k > 1.  The entire
        file is read from the CURRENT POSITION if k < 0. (does not necessarily read from beginning
        of file if previous datagrams were read)
        '''

        if k == 1:
            try:
                return self._read_next_dgram(header=header)
            except Exception:
                if self.at_eof():
                    raise SimradEOF()
                else:
                    raise

        elif k > 0:

            dgram_list = []

            for m in range(k):
                try:
                    dgram = self._read_next_dgram()
                    dgram_list.append(dgram)

                except Exception:
                    break

            return dgram_list

        elif k < 0:
            return self.readall()


    def readall(self):
        '''
        Reads the entire file from the beginning and returns a list of datagrams.
        '''

        self.seek(0, SEEK_SET)
        dgram_list = []

        for raw_dgram in self.iter_dgrams():
            dgram_list.append(raw_dgram)

        return dgram_list


    def _find_next_datagram(self):
        '''
        _find_next_datagram will read raw byte from the file and search for a known
        datagram ID. It will set the file pointer to the beginning of the next valid
        datagram and return. It will raise SimradEOF if it searches to the end of
        the file.
        '''

        #  define the regex pattern used to search for datagrams
        #  (don't search for non-repeating datagrams)
        re_pattern = b'RAW|NME|TAG|BOT|DEP|XML|MRU'

        #  Set the search buffer size in bytes
        search_buf_bytes = 1024 * 1024 * 10

        #  set up
        initial_file_pos = self._tell_bytes()
        current_file_pos = initial_file_pos
        found_match = False
        log.warning('Attempting to find next valid datagram...')

        #  search until a match or the end of the file
        while not found_match:
            #  read some bytes
            buf = self._read_bytes(search_buf_bytes)

            #  check if we're at the end
            if len(buf) == 0:
                raise SimradEOF()

            #  check of a datagram header
            matches = re.finditer(re_pattern, buf)

            for match in matches:
                #  We have found text that matches one of our datagrams

                #  compute the offset to this datagram from the beginning of the file
                #  remembering to subtract the 4 bytes for the datagram size
                next_dgram = current_file_pos + match.start() - 4

                #  issue a warning
                log.warning('Found next datagram:  %s @ %d', match.group().decode('utf-8'), next_dgram)

                #  seek to the datagram
                self._seek_bytes(next_dgram)
                log.warning('%d bytes were skipped.', next_dgram - initial_file_pos)
                found_match = True
                break

            #  update the current position
            current_file_pos = current_file_pos + search_buf_bytes


    def tell(self):
        '''
        Returns the current file pointer offset by datagram number
        '''
        return self._current_dgram_offset


    def get_header(self):
        '''
        Returns the header of the next datagram *without* resetting the file
        position. The file pointer will be pointing at the first byte of the
        datagram payload.

        Use this method to query a datagram type prior to reading or skipping
        a datagram. You must pass the header obtained to your call to read or
        skip to prevent those methods from trying to read the header again.

        This method returns a dict containing the header information along with
        some additional helpful fields:

                type: The datagram type, including version e.g. RAW3, NME0
                low_date: The low 8 bytes of the datagram time as NT time
                high_date: The high 8 bytes of the datagram time as NT time
                timestamp: The datagram time as datetime64
                size: The size of the datagram payload in bytes
                raw_byts: The raw bytes of the header
                bytes_read: The TOTAL bytes of this datagram including size
                            and size check
                channel: (Only for RAW* datagrams) the channel this RAW datagram
                         is associated with

        '''
        #  call peek but don't rewind the pointer
        dgram_header = self.peek(rewind=False)

        #  add some convenience values to the header dict
        us_past_nt_epoch = ((dgram_header['high_date'] << 32) + dgram_header['low_date']) // 10
        dgram_header['timestamp'] = UTC_NT_EPOCH + datetime.timedelta(microseconds=us_past_nt_epoch)
        dgram_header['bytes_read'] = dgram_header['size'] + 20

        return dgram_header


    def peek(self, rewind=True):
        '''
        Returns the header of the next datagram in the file.  The file position is
        reset back to the original location afterwards.

        Set rewind to False to leave the file pointer at the first byte of the
        datagram payload. This is equivalent to the _get_datagram_header() method
        while also including channel information.

        :returns: [dgram_size, dgram_type, (low_date, high_date), channel_id]
        '''

        #  read the next dgram header
        dgram_header = self._read_dgram_header()

        if dgram_header['type'].startswith('RAW0'):
            dgram_header['channel'] = struct.unpack('h', self._read_bytes(2))[0]
            if rewind:
                #  rewind to the beginning of the datagram
                self._seek_bytes(-18, SEEK_CUR)
            else:
                #  rewind to the beginning of the payload
                self._seek_bytes(-2, SEEK_CUR)
        elif dgram_header['type'].startswith('RAW3') or dgram_header['type'].startswith('RAW4'):
            chan_id = struct.unpack('128s', self._read_bytes(128))[0]
            dgram_header['channel_id'] = chan_id.strip(b'\x00')
            if rewind:
                #  rewind to the beginning of the datagram
                self._seek_bytes(-144, SEEK_CUR)
            else:
                #  rewind to the beginning of the payload
                self._seek_bytes(-128, SEEK_CUR)
        else:
            #  The file pointer is always pointing at the payload for non RAW datagrams.
            if rewind:
                self._seek_bytes(-16, SEEK_CUR)

        return dgram_header


    def __next__(self):
        '''
        Returns the next datagram (synonomous with self.read(1))
        '''

        return self.read(1)


    def prev(self):
        '''
        Returns the previous datagram 'behind' the current file pointer position
        '''

        self.skip_back()
        raw_dgram = self.read(1)
        self.skip_back()
        return raw_dgram


    def skip(self, header=None):
        '''
        Skips forward to the next datagram without reading the contents of the current one

        If header is provided, it is assumed that the get_header() method has been called
        and that the file pointer is at the payload.
        '''

        if header is None:
            header = self._read_dgram_header()

        if header['size'] < 16:
            log.warning('Invalid datagram header: size: %d, type: %s, nt_date: %s.  dgram_size < 16',
                header['size'], header['type'], str((header['low_date'], header['high_date'])))

            self._find_next_datagram()

        else:
            #  jump past the datagram payload
            self._seek_bytes(header['size'] - 12, SEEK_CUR)

            #  check the trailing size
            dgram_size_check = self._read_dgram_size()

            if header['size'] != dgram_size_check:
                log.warning('Datagram failed size check:  %d != %d @ (%d, %d)',
                    header['size'], dgram_size_check, self._tell_bytes(), self.tell())
                log.warning('Skipping to next datagram... (in skip)')

                self._find_next_datagram()

        #  increment our datagram counter
        self._current_dgram_offset += 1


    def skip_back(self):
        '''
        Skips backwards to the previous datagram without reading it's contents

        THIS IS PROBABLY BROKEN
        '''

        old_file_pos = self._tell_bytes()

        try:
            self._seek_bytes(-4, SEEK_CUR)
        except IOError:
            raise

        dgram_size_check = self._read_dgram_size()

        #Seek to the beginning of the datagram and read as normal
        try:
            self._seek_bytes(-(8 + dgram_size_check), SEEK_CUR)
        except IOError:
            raise DatagramSizeError

        try:
            dgram_size = self._read_dgram_size()

        except DatagramSizeError:
            print('Error reading the datagram')
            self._seek_bytes(old_file_pos, SEEK_SET)
            raise

        if dgram_size_check != dgram_size:
            self._seek_bytes(old_file_pos, SEEK_SET)
            raise DatagramSizeError
        else:
            self._seek_bytes(-4, SEEK_CUR)

        self._current_dgram_offset -= 1


    def iter_dgrams(self):
        '''
        Iterates through the file, repeatedly calling self.next() until
        the end of file is reached
        '''

        while True:
            # new_dgram = self.next()
            # yield new_dgram

            try:
                new_dgram = next(self)
            except Exception:
                log.debug('Caught EOF?')
                raise StopIteration

            yield new_dgram


    #Unsupported members
    def readline(self):
        '''
        aliased to self.next()
        '''
        return next(self)


    def readlines(self):
        '''
        aliased to self.read(-1)
        '''
        return self.read(-1)


    def seek(self, offset, whence):
        '''
        Performs the familiar 'seek' operation using datagram offsets
        instead of raw bytes.
        '''

        if whence == SEEK_SET:
            if offset < 0:
                raise ValueError('Cannot seek backwards from beginning of file')
            else:
                self._seek_bytes(0, SEEK_SET)
                self._current_dgram_offset = 0
        elif whence == SEEK_END:
            if offset > 0:
                raise ValueError('Use negative offsets when seeking backward from end of file')

            #Do we need to generate the total number of datagrams w/in the file?
            try:
                self._set_total_dgram_count()
                #Throws a value error if _total_dgram_count has alread been set.  We can ignore it
            except ValueError:
                pass

            self._seek_bytes(0, SEEK_END)
            self._current_dgram_offset = self._total_dgram_count

        elif whence == SEEK_CUR:
            pass
        else:
            raise ValueError('Illegal value for \'whence\' (%s), use 0 (beginning), 1 (current), or 2 (end)' % (str(whence)))

        if offset > 0:
            for k in range(offset):
                self.skip()
        elif offset < 0:
            for k in range(-offset):
                self.skip_back()


    def reset(self):
        self._current_dgram_offset = 0
        self._total_dgram_count = None
        self._seek_bytes(0, SEEK_SET)
