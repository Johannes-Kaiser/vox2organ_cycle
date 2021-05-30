
""" Hippocampus dataset """

__author__ = "Fabi Bongratz"
__email__ = "fabi.bongratz@gmail.com"

import os
import random
import logging
import torch

import numpy as np
import nibabel as nib
import trimesh

from utils.modes import DataModes, ExecModes
from utils.mesh import Mesh
from utils.eval_metrics import Jaccard
from utils.logging import write_img_if_debug
from utils.utils import (
    create_mesh_from_voxels,
    normalize_min_max,
    unnormalize_vertices,
    sample_inner_volume_in_voxel
)
from data.dataset import (
    DatasetHandler,
    augment_data,
    img_with_patch_size,
    sample_surface_points
)

class Hippocampus(DatasetHandler):
    """ Hippocampus dataset from
    https://drive.google.com/file/d/1RzPB1_bqzQhlWvU-YGvZzhx2omcDh38C/view

    It loads all data specified by 'ids' directly into memory. Only ids in
    'imagesTr' are considered (for 'imagesTs' no labels exist).

    :param list ids: The ids of the files the dataset split should contain, example:
        ['hippocampus_101', 'hippocampus_212',...]
    :param DataModes datamode: TRAIN, VALIDATION, or TEST
    :param str raw_data_dir: The raw base folder, contains e.g. subfolders
    imagesTr/ and labelsTr/
    :param str preprocessed_data_dir: Pre-processed data, e.g. meshes created with
    marching cubes.
    :param patch_size: The patch size of the images, e.g. (64, 64, 64)
    :param augment: Use image augmentation during training if 'True'
    :param bool load_mesh: Load a precomputed mesh. Possible values are
    'folder': Load meshes from a folder defined by 'preprocessed_data_dir',
    'create': create all meshes with marching cubes. 'no': Do not store meshes.
    :param mc_step_size: The step size for marching cubes generation, only
    relevant if load_mesh is 'create'.
    :param mesh_target_type: The type of mesh target, either 'mesh' or
    'pointcloud'
    """

    def __init__(self, ids: list, mode: DataModes, raw_data_dir: str,
                 preprocessed_data_dir: str, patch_size, augment: bool,
                 mesh_target_type: str,
                 load_mesh='no', mc_step_size=1):
        super().__init__(ids, mode)

        self._raw_data_dir = raw_data_dir
        self._preprocessed_data_dir = preprocessed_data_dir
        self._augment = augment
        self._mc_step_size = mc_step_size
        self._mesh_target_type = mesh_target_type
        self.n_v_classes = 2
        self.n_m_classes = 1
        self.patch_size = patch_size

        self.data = self._load_data3D(folder="imagesTr")
        # NORMALIZE images (hippocampus data varies several orders of
        # magnitude)!
        for i, d in enumerate(self.data):
            self.data[i] = normalize_min_max(d)

        self.voxel_labels = self._load_data3D(folder="labelsTr")
        # Ignore distinction between anterior and posterior hippocampus
        for vl in self.voxel_labels:
            vl[vl > 1] = 1

        if load_mesh == 'folder':
            # Load all meshes from a folder
            self.mesh_labels = self._load_dataMesh(folder="meshlabelsTr")
        elif load_mesh == 'create':
            # Create all meshes with marching cubes
            self.mesh_labels = self._calc_mesh_labels_all()
        else:
            # Do not store mesh labels
            self.mesh_labels = None

        self.check_data()

        assert self.__len__() == len(self.data)
        assert self.__len__() == len(self.voxel_labels)
        if load_mesh != 'no':
            assert self.__len__() == len(self.mesh_labels)

    @staticmethod
    def split(raw_data_dir, preprocessed_data_dir, dataset_seed,
              dataset_split_proportions, patch_size, augment_train, save_dir, **kwargs):
        """ Create train, validation, and test split of the Hippocampus data"

        :param str raw_data_dir: The raw base folder, contains e.g. subfolders
        imagesTr/ and labelsTr/
        :param str preprocessed_data_dir: Pre-processed data, e.g. meshes created with
        marching cubes.
        :param dataset_seed: A seed for the random splitting of the dataset.
        :param dataset_split_proportions: The proportions of the dataset
        splits, e.g. (80, 10, 10)
        :patch_size: The patch size of the 3D images.
        :augment_train: Augment training data.
        :save_dir: A directory where the split ids can be saved.
        :overfit: All three splits are the same and contain only one element.
        :return: (Train dataset, Validation dataset, Test dataset)
        """

        overfit = kwargs.get("overfit", False)
        mesh_target_type = kwargs.get("mesh_target_type", "pointcloud")

        # Available files
        all_files = os.listdir(os.path.join(raw_data_dir, "imagesTr"))
        all_files = [fn for fn in all_files if "._" not in fn] # Remove invalid
        all_files = [fn.split(".")[0] for fn in all_files] # Remove file ext.

        # Shuffle with seed
        random.Random(dataset_seed).shuffle(all_files)

        # Split
        if overfit:
            # Only consider first element of available data
            indices_train = slice(0, 1)
            indices_val = slice(0, 1)
            indices_test = slice(0, 1)
        else:
            # No overfit
            assert np.sum(dataset_split_proportions) == 100, "Splits need to sum to 100."
            indices_train = slice(0, dataset_split_proportions[0] * len(all_files) // 100)
            indices_val = slice(indices_train.stop,
                                indices_train.stop +\
                                    (dataset_split_proportions[1] * len(all_files) // 100))
            indices_test = slice(indices_val.stop, len(all_files))

        # Create datasets
        train_dataset = Hippocampus(all_files[indices_train],
                                    DataModes.TRAIN,
                                    raw_data_dir,
                                    preprocessed_data_dir,
                                    patch_size,
                                    augment_train,
                                    mesh_target_type,
                                    load_mesh='no') # no meshes for train
        val_dataset = Hippocampus(all_files[indices_val],
                                  DataModes.VALIDATION,
                                  raw_data_dir,
                                  preprocessed_data_dir,
                                  patch_size,
                                  False, # no augment for val
                                  mesh_target_type,
                                  load_mesh='create') # create all mc meshes
        test_dataset = Hippocampus(all_files[indices_test],
                                  DataModes.TEST,
                                  raw_data_dir,
                                  preprocessed_data_dir,
                                  patch_size,
                                  False, # no augment for test
                                  mesh_target_type,
                                  load_mesh='create') # create all mc meshes

        # Save ids to file
        DatasetHandler.save_ids(all_files[indices_train], all_files[indices_val],
                         all_files[indices_test], save_dir)

        return train_dataset, val_dataset, test_dataset

    def __len__(self):
        return len(self._files)

    def get_item_from_index(self, index: int):
        """
        One data item has the form
        (3D input image, 3D voxel label, pointcloud)
        with types
        (torch.tensor, torch.tensor, torch.tensor)
        """
        img = self.data[index]
        voxel_label = self.voxel_labels[index]

        # Potentially augment
        if self._augment:
            img, voxel_label = augment_data(img, voxel_label)

        # Fit patch size
        img = img_with_patch_size(img, self.patch_size, False)[None]
        voxel_label = img_with_patch_size(voxel_label, self.patch_size,
                                          True)

        # Surface points or mesh
        if self._mesh_target_type == 'pointcloud':
            target_points = sample_surface_points(voxel_label, self.n_v_classes)
            target_faces = np.array([]) # Empty
        else:
            mesh_target = self._get_mesh(index, voxel_label)
            target_points = mesh_target.vertices[None]
            target_faces = mesh_target.faces[None]

        logging.getLogger(ExecModes.TRAIN.name).debug("Dataset file %s",
                                                      self._files[index])

        return img, voxel_label, target_points, target_faces

    def get_item_and_mesh_from_index(self, index: int):
        """ One data item and a corresponding mesh.
        Data is returned in the form
        (image, voxel label, mesh)
        """
        img, voxel_label, _, _ = self.get_item_from_index(index)
        mesh_label = self._get_mesh(index, voxel_label)

        return img, voxel_label, mesh_label

    def _load_data3D(self, folder: str):
        data_dir = os.path.join(self._raw_data_dir, folder)
        data = []
        for fn in self._files:
            d = nib.load(os.path.join(data_dir, fn + ".nii.gz")).get_fdata()
            data.append(d)

        return data

    def _calc_mesh_labels_all(self):
        meshes = []
        for v in self.voxel_labels:
            vp = img_with_patch_size(v, self.patch_size, True)
            meshes.append(create_mesh_from_voxels(vp))

        return meshes

    def _get_mesh(self, index=None, voxel_label=None):
        if index is None and voxel_label is None:
            raise RuntimeError("Cannot load mesh with index and voxel label"\
                               " being 'None'.")
        if self.mesh_labels is not None and not self._augment: # read
            mesh_label = self.mesh_labels[index]
        else:
            # Generate from given voxel label. Handing in the label directly is
            # necessary since it may be different from self.voxel_labels[index] due to
            # augmentation.
            mesh_label = create_mesh_from_voxels(voxel_label,
                                                   self._mc_step_size)

        return mesh_label

    def _load_dataMesh(self, folder):
        data_dir = os.path.join(self._preprocessed_data_dir, folder)
        data = []
        for fn in self._files:
            d = trimesh.load_mesh(os.path.join(data_dir, fn + ".ply"))
            data.append(d)

        return data

    def check_data(self):
        """ Check if voxel and mesh data is consistent """
        _, voxel_label, mesh = self.get_item_and_mesh_from_index(0)
        shape = torch.tensor(voxel_label.shape)[None]
        vertices, faces = mesh.vertices, mesh.faces
        voxelized_mesh = torch.zeros_like(voxel_label, dtype=torch.long)
        vertices = vertices.view(self.n_m_classes, -1, 3)
        faces = faces.view(self.n_m_classes, -1, 3)
        unnorm_verts = unnormalize_vertices(
            vertices.view(-1, 3), shape
        ).view(self.n_m_classes, -1, 3)
        pv = Mesh(unnorm_verts,
                  faces).get_occupied_voxels(shape.squeeze().cpu().numpy())
        if pv is not None:
            pv_flip = np.flip(pv, axis=1)  # convert x,y,z -> z, y, x
            # Occupied voxels are considered to belong to one class
            voxelized_mesh[pv_flip[:,0], pv_flip[:,1], pv_flip[:,2]] = 1
        else:
            # No mesh in the valid range predicted --> keep zeros
            pass

        # Strip outer layer of voxelized mesh
        strip = True
        if strip:
            voxelized_mesh = sample_inner_volume_in_voxel(voxelized_mesh)

        j_vox = Jaccard(voxel_label.cuda(), voxelized_mesh.cuda(), 2)

        assert j_vox > 0.9,\
                "Voxelized mesh and voxel label should have a large IoU."

        logging.getLogger(ExecModes.TRAIN.name).debug(
            "IoU of voxel and mesh label: %.4f", j_vox
        )
        write_img_if_debug(voxel_label.squeeze().cpu().numpy(),
                           "../misc/data_voxel_label.nii.gz")
        write_img_if_debug(voxelized_mesh.squeeze().cpu().numpy(),
                           "../misc/hippo_mesh_label.nii.gz")
