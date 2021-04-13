#!/usr/bin/env python3

""" Main file """

from argparse import ArgumentParser, RawTextHelpFormatter

from utils.params import hyper_ps_default
from utils.modes import ExecModes
from utils.utils import update_dict

from utils.train import training_routine
from utils.test import test_routine
from utils.train_test import train_test_routine

# Define parameters
hyper_ps = {
    #######################
    'EXPERIMENT_NAME': None,  # Attention: "debug" overwrites previous dir"
                              # should be set with console argument
    #######################
    # Learning
    'OPTIM_PARAMS': {'lr': 0.0003},
    'BATCH_SIZE': 64,

    # Data directories
    'RAW_DATA_DIR': "/mnt/nas/Data_Neuro/Task04_Hippocampus/",
    'PREPROCESSED_DATA_DIR': "/home/fabianb/data/preprocessed/Task04_Hippocampus/"
}

mode_handler = {
    ExecModes.TRAIN.value: training_routine,
    ExecModes.TEST.value: test_routine,
    ExecModes.TRAIN_TEST.value: train_test_routine
}


def main(hps):
    """
    Main function for training, validation, test
    """
    argparser = ArgumentParser(description="cortex-parcellation-using-meshes",
                               formatter_class=RawTextHelpFormatter)
    argparser.add_argument('algorithm',
                           type=str,
                           help="The name of the algorithm. Supported:\n"
                           "- voxel2mesh")
    argparser.add_argument('dataset',
                           type=str,
                           help="The name of the dataset. Supported:\n"
                           "- Hippocampus")
    argparser.add_argument('--train',
                           action='store_true',
                           help="Train a model.")
    argparser.add_argument('--test',
                           action='store_true',
                           help="Test a model.")
    argparser.add_argument('-v', '--verbose',
                           dest = 'verbose',
                           action='store_true',
                           help="Debug output.")
    argparser.add_argument('-n', '--exp_name',
                           dest='exp_name',
                           type=str,
                           default=None,
                           help="Name of experiment:\n"
                           "- 'debug' means that the results are  written "
                           "into a directory \nthat might be overwritten "
                           "later. This may be useful for debugging \n"
                           "where the experiment result does not matter.\n"
                           "- Any other name cannot overwrite an existing"
                           " directory.\n"
                           "- If not specified, experiments are automatically"
                           " enumerated with exp_i.")
    args = argparser.parse_args()
    hps['EXPERIMENT_NAME'] = args.exp_name
    hps['DATASET'] = args.dataset

    # Fill hyperparameters with defaults
    hps = update_dict(hyper_ps_default, hps)

    if args.train and not args.test:
        mode = ExecModes.TRAIN.value
    if args.test and not args.train:
        mode = ExecModes.TEST.value
    if args.train and args.test:
        mode = ExecModes.TRAIN_TEST.value
    if not args.test and not args.train:
        print("Please use either --train or --test or both.")
        return

    # Run
    routine = mode_handler[mode]
    routine(hps, experiment_name=hps['EXPERIMENT_NAME'], verbose=args.verbose)


if __name__ == '__main__':
    main(hyper_ps)
