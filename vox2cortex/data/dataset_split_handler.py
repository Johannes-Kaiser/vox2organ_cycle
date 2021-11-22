
""" Convenient dataset splitting. Add new split functions here.  """

__author__ = "Fabi Bongratz"
__email__ = "fabi.bongratz@gmail.com"

from data.supported_datasets import SupportedDatasets
from data.hippocampus import Hippocampus
from data.cortex import Cortex

# Mapping supported datasets to split functions
dataset_split_handler = {
    SupportedDatasets.ADNI_CSR_large.name: Cortex.split,
}

