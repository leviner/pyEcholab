# coding=utf-8

#    National Oceanic and Atmospheric Administration
#    Alaskan Fisheries Science Center
#    Resource Assessment and Conservation Engineering
#    Midwater Assessment and Conservation Engineering

# THIS SOFTWARE AND ITS DOCUMENTATION ARE CONSIDERED TO BE IN THE PUBLIC DOMAIN
# AND THUS ARE AVAILABLE FOR UNRESTRICTED PUBLIC USE. THEY ARE FURNISHED "AS
# IS." THE AUTHORS, THE UNITED STATES GOVERNMENT, ITS INSTRUMENTALITIES,
# OFFICERS, EMPLOYEES, AND AGENTS MAKE NO WARRANTY, EXPRESS OR IMPLIED, AS TO
# THE USEFULNESS OF THE SOFTWARE AND DOCUMENTATION FOR ANY PURPOSE.  THEY
# ASSUME NO RESPONSIBILITY (1) FOR THE USE OF THE SOFTWARE AND DOCUMENTATION;
# OR (2) TO PROVIDE TECHNICAL SUPPORT TO USERS.

'''
.. module:: echolab2.instruments.util.simrad_utils

    :synopsis:  This module provides helper functions for working with Simrad
                echosounder data.

| Developed by:  Rick Towler   <rick.towler@noaa.gov>
| National Oceanic and Atmospheric Administration (NOAA)
| Alaska Fisheries Science Center (AFSC)
| Midwater Assesment and Conservation Engineering Group (MACE)
|
| Authors:
|       Rick Towler   <rick.towler@noaa.gov>
| Maintained by:
|       Rick Towler   <rick.towler@noaa.gov>

$Id$
'''

import os


def get_simrad_bottom_files(datafile_name, data_object, prefer_xyz=True, skip_xyz=False):
    '''get_simrad_bottom_files searches for XYZ and/or .bot files that are adjacent
    to the provided datafile and returns the file type (BOT or XYZ) and the filename(s).
    This function does not read the files. It just checks for bottom files that are
    following the standard naming conventions and returns the results.

    If both XYZ and .bot files are present, by default this function will return XYZ
    files. This function does not search for .out files.

    datafile_name (str): the full path to the .raw file that you wish to get the
        matching bottom data files for.
    data_object (echolab2.EK60 or echolab2.EK80): Pass the EK60 or EK80 data object
        that you will be using to read in the data files.
    prefer_xyz (bool): Set this to True to look for XYZ files first and only look for
        a .bot file if *NO* XYZ files exist.

    skip_xyz (bool): Set this to True to skip checking for XYZ files. This would be
        appropriate when working with EK/ES60 data where XYZ files are not generated.
    '''

    #  get the base file name
    basename = os.path.splitext(datafile_name)[0]

    type = None
    bottom_files = None

    if not skip_xyz and prefer_xyz:
        bottom_files = get_xyz_filenames(basename, data_object)
        if len(bottom_files) == 0:
            bottom_files = get_bot_filename(basename)
            if bottom_files:
                type = 'BOT'
        else:
            #  for now we
            type = 'XYZ'
    else:
        bottom_files = get_bot_filename(basename)
        type = 'BOT'

        if not bottom_files and not skip_xyz:
            bottom_files = get_xyz_filenames(basename, data_object)
            type = 'XYZ'

    return type, bottom_files


def get_xyz_filenames(basename, data_object, raw_index=0):

    #  look for XYZ files adjacent to the datafile
    xyz_files = {}
    for channel_id in data_object.raw_data:

        #  By default we assume the user is working with a single data type and
        #  we grab the first raw_data object in the channel's list of raw objects.
        #  If you're reading multiple files containing different data types, you
        #  may need to set the raw_index value when calling this function.
        raw_obj = data_object.raw_data[channel_id][raw_index]

        #  check for a MUX/Sequence ID
        mux_id = channel_id.split('_')
        if len(mux_id) > 1:
            mux_id = mux_id[1]
        else:
            mux_id = ''

        #  generate the xyz file channel id using the "short" id and
        #  replacing the colon (illegal filename character) with a space
        xyz_id = raw_obj.configuration[-1]['channel_id_short'].replace(':',' ')

        #  add the mux/sequence ID
        if mux_id:
            xyz_id += '-' + mux_id

        #  and build the xyz filename
        xyz_filename = basename + '-' + xyz_id + '.XYZ'

        #  check if this file exists
        if os.path.isfile(xyz_filename):
            xyz_files[channel_id] = xyz_filename

    return xyz_files


def get_bot_filename(basename):

    bot_file = None

    #  build the bot filename
    bot_filename = basename + '.bot'

    #  check if this file exists
    if os.path.isfile(bot_filename):
        bot_file = bot_filename
    else:
        #  in rare cases the bot filename will be off by a second, check for this here
        try:
            sec_base = int(basename[-1])
            basename = basename[:-1]

            sec_val = str(sec_base + 1)
            bot_filename = basename + sec_val + '.bot'
            if os.path.isfile(bot_filename):
                bot_file = bot_filename
            else:
                sec_val = str(sec_base - 1)
                bot_filename = basename + sec_val + '.bot'
                if os.path.isfile(bot_filename):
                    bot_file = bot_filename
        except:
            pass

    return bot_file
