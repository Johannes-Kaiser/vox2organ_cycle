""" Visualization of raw images """

__author__ = "Fabi Bongratz"
__email__ = "fabi.bongratz@gmail.com"

import os

from argparse import ArgumentParser
from utils.visualization import show_img_slices_3D

def vis_img3D():
    parser = ArgumentParser(description="Visualize 3D image data.")
    parser.add_argument('filenames',
                        nargs='+',
                        type=str,
                        help="The filenames or the name of one folder to visualize.")
    parser.add_argument('--nolabel',
                        dest='show_label',
                        action='store_false',
                        help="Disable visualization of ground truth labels.")

    args = parser.parse_args()
    if os.path.isdir(args.filenames[0]):
        filenames = args.filenames[0]
    else:
        filenames = args.filenames
    show_img_slices_3D(filenames, args.show_label)

if __name__ == "__main__":
    vis_img3D()
