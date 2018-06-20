# Copyright 2017 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


"""
This tools is able to pick some frames from a folder
and to copy or move them to an other folder

Only usefull if used with the --limit flag unless it will copy the whole folder
"""

import argparse
import glob
import ntpath
import os
from random import shuffle
from shutil import copyfile, move

PARSER = argparse.ArgumentParser(description='')
PARSER.add_argument(
    '--path_in',
    dest='path_in',
    default='',
    help='folder where the pictures are',
    required=True)
PARSER.add_argument(
    '--path_out', dest='path_out', default='./', help='Destination folder')
PARSER.add_argument(
    '--delete',
    dest='delete',
    action='store_true',
    help='use this flag to delete the orginal file after conversion')
PARSER.set_defaults(delete=False)
PARSER.add_argument(
    '--limit',
    dest='limit',
    type=int,
    default=-1,
    help='cap the number of generated pairs')
ARGS = PARSER.parse_args()


def random_pick(path_in, path_out, limit, delete):
    """Pick a random set of jpg files and copy them to an other folder

    Args:
        path_in: the folder that contains the files
        path_out: the folder to export the picked files

    Returns:
        nothing

    Raises:
        nothing
    """
    if path_in == path_out:
        print 'path in == path out, that is not allowed, quiting'
        quit()

    path = '{}/*'.format(path_in)
    print 'looking for all files in', path
    files = glob.glob(path)
    file_count = len(files)
    print 'found ', file_count, 'files'
    if limit > 0:
        print 'will use limit of', limit, 'files'
        shuffle(files)
        files = files[:limit]

    i = 0
    for image_file in files:
        i = i + 1
        basename = ntpath.basename(image_file)
        file_out = os.path.join(path_out, basename)

        try:
            if delete:
                print i, '/', limit, '  moving', image_file, 'to', file_out
                move(image_file, file_out)
            else:
                print i, '/', limit, '  copying', image_file, 'to', file_out
                copyfile(image_file, file_out)
        except:
            print '''can't pick file''', image_file, 'to', file_out


if __name__ == '__main__':
    random_pick(ARGS.path_in, ARGS.path_out, ARGS.limit, ARGS.delete)
