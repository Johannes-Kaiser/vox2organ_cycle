
""" Cortex dataset handler """

__author__ = "Fabi Bongratz"
__email__ = "fabi.bongratz@gmail.com"

import os
import random
import logging
from enum import IntEnum

import torch
import torch.nn.functional as F
import numpy as np
import nibabel as nib
import trimesh
from trimesh import Trimesh
from trimesh.scene.scene import Scene
from pytorch3d.structures import Meshes

from utils.modes import DataModes, ExecModes
from utils.logging import measure_time
from utils.mesh import Mesh, generate_sphere_template
from utils.utils import (
    voxelize_mesh,
    create_mesh_from_voxels,
    unnormalize_vertices,
    sample_inner_volume_in_voxel,
    normalize_min_max,
    normalize_vertices,
    mirror_mesh_at_plane
)
from data.dataset import (
    DatasetHandler,
    augment_data,
    img_with_patch_size,
    offset_due_to_padding
)

class CortexLabels(IntEnum):
    right_white_matter = 41
    left_white_matter = 2
    left_cerebral_cortex = 3
    right_cerebral_cortex = 42

def combine_labels(labels, names):
    """ Only consider labels in 'names' and set all those labels equally to 1
    """
    ids = [CortexLabels[n].value for n in names]

    return np.isin(labels, ids).astype(int)

class Cortex(DatasetHandler):
    """ Cortex dataset

    It loads all data specified by 'ids' directly into memory.

    :param list ids: The ids of the files the dataset split should contain, example:
        ['1000_3', '1001_3',...]
    :param DataModes datamode: TRAIN, VALIDATION, or TEST
    :param str raw_data_dir: The raw base folder, contains folders
    corresponding to sample ids
    :param augment: Use image augmentation during training if 'True'
    :param patch_size: The patch size of the images, e.g. (256, 256, 256)
    :param mesh_target_type: 'mesh' or 'pointcloud'
    :param n_ref_points_per_structure: The number of ground truth points
    per 3D structure.
    :param structure_type: Either 'white_matter' or 'cerebral_cortex'
    :param patch_mode: Whether to extract patches from the data.
    :param patch_origin: The anker of an extracted patch, only has an effect if
    patch_mode is True.
    :param select_patch_size: The size of the cut out patches. Can be different
    to patch_size, e.g., if extracted patches should be resized after
    extraction.
    :param mc_step_size: The marching cubes step size.
    """

    def __init__(self, ids: list, mode: DataModes, raw_data_dir: str,
                 augment: bool, patch_size, mesh_target_type: str,
                 n_ref_points_per_structure: int, structure_type: str,
                 patch_mode: bool, patch_origin=(0,0,0), select_patch_size=None,
                 **kwargs):
        super().__init__(ids, mode)

        if augment:
            raise NotImplementedError("Cortex dataset does not support"\
                                      " augmentation at the moment.")
        if structure_type == "cerebral_cortex":
            seg_label_names = 'all' # all present labels are combined
            mesh_label_names = ("rh_pial", "lh_pial")
        elif structure_type == "white_matter":
            if patch_mode:
                seg_label_names = ("right_white_matter",)
                mesh_label_names = ("rh_white",)
            else:
                seg_label_names = ("voxelized_mesh", "voxelized_mesh")
                mesh_label_names = ("lh_white", "rh_white")
        else:
            raise ValueError("Unknown structure type.")

        self.structure_type = structure_type
        self._raw_data_dir = raw_data_dir
        self._augment = augment
        self._mesh_target_type = mesh_target_type
        self._patch_origin = patch_origin
        self._mc_step_size = kwargs.get('mc_step_size', 1)
        self.patch_mode = patch_mode
        self.patch_size = patch_size
        self.select_patch_size = select_patch_size if (
            select_patch_size is not None) else patch_size
        self.n_m_classes = len(mesh_label_names)
        assert self.n_m_classes == kwargs.get("n_m_classes",
                                              len(mesh_label_names)),\
                "Number of mesh classes incorrect."
        self.n_ref_points_per_structure = n_ref_points_per_structure
        self.mesh_label_names = mesh_label_names
        self.n_structures = len(mesh_label_names)
        # Vertex labels are combined into one class (and background)
        self.n_v_classes = 2

        # Image data
        self.data = self._load_data3D(filename="mri.nii.gz", is_label=False)
        # NORMALIZE images
        for i, d in enumerate(self.data):
            self.data[i] = normalize_min_max(d)

        # Mesh labels if not patch mode
        assert not patch_mode or 'voxelized_mesh' not in seg_label_names,\
            "Voxelized mesh not possible as voxel ground truth in patch mode."
        assert not patch_mode or len(seg_label_names) == 1,\
                "Can only use one segmentation class in patch mode."
        if not patch_mode:
            self.mesh_labels, (self.centers, self.radii) =\
                    self._load_dataMesh(meshnames=mesh_label_names)

        # Voxel labels
        if 'voxelized_mesh' in seg_label_names:
            self.voxel_labels = self._create_voxel_labels_from_meshes()
        else:
            self.voxel_labels = self._load_data3D(filename="aseg.nii.gz",
                                                  is_label=True)
            if seg_label_names == "all":
                for vl in self.voxel_labels:
                    vl[vl > 1] = 1
            else:
                self.voxel_labels = [
                    combine_labels(l, seg_label_names) for l in self.voxel_labels
                ]

        # Mesh labels if patch mode
        if patch_mode:
            self.mesh_labels = self._load_mc_dataMesh()

        assert self.__len__() == len(self.data)
        assert self.__len__() == len(self.voxel_labels)
        assert self.__len__() == len(self.mesh_labels)

    def store_sphere_template(self, path):
        """ Template for dataset. This can be stored and later used during
        training.
        """
        if self.centers is not None and self.radii is not None:
            template = generate_sphere_template(self.centers,
                                                self.radii,
                                                level=6)
            template.export(path)
        else:
            raise RuntimeError("Centers and/or radii are unknown, template"
                               " cannnot be created. ")

    def store_index0_template(self, path, n_max_points=41000):
        """ This template is the structure of dataset element at index 0,
        potentially mirrored at the hemisphere plane. """
        template = Scene()
        if len(self.mesh_label_names) == 2:
            label_1, label_2 = self.mesh_label_names
        else:
            label_1 = self.mesh_labels[0]
            label_2 = None
        # Select mesh to generate the template from
        vertices = self.mesh_labels[0].vertices[0]
        faces = self.mesh_labels[0].faces[0]

        # Remove padded vertices
        valid_ids = np.unique(faces)
        valid_ids = valid_ids[valid_ids != -1]
        vertices_ = vertices[valid_ids]

        structure_1 = Trimesh(vertices_, faces, process=False)

        # Increase granularity until desired number of points is reached
        while structure_1.subdivide().vertices.shape[0] < n_max_points:
            structure_1 = structure_1.subdivide()

        assert structure_1.is_watertight, "Mesh template should be watertight."
        print(f"Template structure has {structure_1.vertices.shape[0]}"
              " vertices.")
        template.add_geometry(structure_1, geom_name=label_1)

        # Second structure = mirror of first structure
        if label_2 is not None:
            plane_normal = np.array(self.centers[label_2] - self.centers[label_1])
            plane_point = 0.5 * np.array((self.centers[label_1] +
                                          self.centers[label_2]))
            structure_2 = mirror_mesh_at_plane(structure_1, plane_normal,
                                              plane_point)
            template.add_geometry(structure_2, geom_name=label_2)

        template.export(path)

        return path

    def store_convex_cortex_template(self, path, n_min_points=40000, n_max_points=41000):
        """ This template is created as follows:
            1. Take the convex hull of one of the two structures and subdivide
            faces until the required number of vertices is large enough
            2. Mirror this mesh on the plane that separates the two cortex
            hemispheres
            3. Store both meshes together in one template
        """
        n_points = 0
        i = 0
        while n_points > n_max_points or n_points < n_min_points:
            if i >= len(self):
                print("Template with the desired number of vertices could not"
                      " be created. Aborting.")
                return None
            template = Scene()
            if len(self.mesh_label_names) == 2:
                label_1, label_2 = self.mesh_label_names
            else:
                label_1 = self.mesh_labels[0]
                label_2 = None
            # Select mesh to generate the template from
            vertices = self.mesh_labels[i].vertices[0]
            faces = self.mesh_labels[i].faces[0]

            # Remove padded vertices
            valid_ids = np.unique(faces)
            valid_ids = valid_ids[valid_ids != -1]
            vertices_ = vertices[valid_ids]

            # Get convex hull of the mesh label
            structure_1 = Trimesh(vertices_, faces, process=False).convex_hull

            # Increase granularity until desired number of points is reached
            while structure_1.subdivide().vertices.shape[0] < n_max_points:
                structure_1 = structure_1.subdivide()

            assert structure_1.is_watertight, "Mesh template should be watertight."
            n_points = structure_1.vertices.shape[0]
            print(f"Template structure {i} has {n_points} vertices.")
            i += 1
        template.add_geometry(structure_1, geom_name=label_1)

        # Second structure = mirror of first structure
        if label_2 is not None:
            plane_normal = np.array(self.centers[label_2] - self.centers[label_1])
            plane_point = 0.5 * np.array((self.centers[label_1] +
                                          self.centers[label_2]))
            structure_2 = mirror_mesh_at_plane(structure_1, plane_normal,
                                              plane_point)
            template.add_geometry(structure_2, geom_name=label_2)

        template.export(path)

        return path

    @staticmethod
    def split(raw_data_dir, dataset_seed, dataset_split_proportions,
              augment_train, save_dir, overfit=None, **kwargs):
        """ Create train, validation, and test split of the cortex data"

        :param str raw_data_dir: The raw base folder, contains a folder for each
        sample
        :param dataset_seed: A seed for the random splitting of the dataset.
        :param dataset_split_proportions: The proportions of the dataset
        splits, e.g. (80, 10, 10)
        :param augment_train: Augment training data.
        :param save_dir: A directory where the split ids can be saved.
        :param overfit: Create small datasets for overfitting.
        :param kwargs: Dataset parameters.
        :return: (Train dataset, Validation dataset, Test dataset)
        """

        # Available files
        all_files = os.listdir(raw_data_dir)
        all_files = [fn for fn in all_files if "meshes" not in fn] # Remove invalid

        # Shuffle with seed
        random.Random(dataset_seed).shuffle(all_files)

        # Split
        if overfit:
            # Only consider first element of available data
            indices_train = slice(0, overfit)
            indices_val = slice(0, overfit)
            indices_test = slice(0, overfit)
        else:
            # No overfit
            assert np.sum(dataset_split_proportions) == 100, "Splits need to sum to 100."
            indices_train = slice(0, dataset_split_proportions[0] * len(all_files) // 100)
            indices_val = slice(indices_train.stop,
                                indices_train.stop +\
                                    (dataset_split_proportions[1] * len(all_files) // 100))
            indices_test = slice(indices_val.stop, len(all_files))

        # Create datasets
        train_dataset = Cortex(all_files[indices_train],
                               DataModes.TRAIN,
                               raw_data_dir,
                               augment=augment_train,
                               **kwargs)
        val_dataset = Cortex(all_files[indices_val],
                             DataModes.VALIDATION,
                             raw_data_dir,
                             augment=False,
                             **kwargs)
        test_dataset = Cortex(all_files[indices_test],
                              DataModes.TEST,
                              raw_data_dir,
                              augment=False,
                              **kwargs)

        # Save ids to file
        DatasetHandler.save_ids(all_files[indices_train], all_files[indices_val],
                         all_files[indices_test], save_dir)

        return train_dataset, val_dataset, test_dataset

    def __len__(self):
        return len(self._files)

    @measure_time
    def get_item_from_index(self, index: int):
        """
        One data item has the form
        (3D input image, 3D voxel label, points)
        with types
        (torch.tensor, torch.tensor, torch.tensor)
        """
        img = self.data[index]
        voxel_label = self.voxel_labels[index]
        target_points,\
                target_faces,\
                target_normals = self._get_mesh_target(index)

        # TODO: implement augmentation

        # Fit patch size
        img = img_with_patch_size(img, self.patch_size, False)[None]
        voxel_label = img_with_patch_size(voxel_label, self.patch_size, True)

        logging.getLogger(ExecModes.TRAIN.name).debug("Dataset file %s",
                                                      self._files[index])

        return img, voxel_label, target_points, target_faces, target_normals

    def _get_mesh_target(self, index):
        """ Ground truth points and optionally normals """
        if self._mesh_target_type == 'pointcloud':
            points = self.mesh_labels[index].vertices
            normals = np.array([]) # Empty, not used
            faces = np.array([]) # Empty, not used
            perm = torch.randperm(points.shape[1])
            perm = perm[:self.n_ref_points_per_structure]
            points = points[:,perm,:]
        elif self._mesh_target_type == 'mesh':
            points = self.mesh_labels[index].vertices
            normals = self.mesh_labels[index].normals
            faces = np.array([]) # Empty, not used
            perm = torch.randperm(points.shape[1])
            perm = perm[:self.n_ref_points_per_structure]
            points = points[:,perm,:]
            normals = normals[:,perm,:]
        else:
            raise ValueError("Invalid mesh target type.")

        return points, faces, normals

    def get_item_and_mesh_from_index(self, index):
        """ Get image, segmentation ground truth and reference mesh"""
        img, voxel_label, _, _, _ = self.get_item_from_index(index)
        mesh_label = self.mesh_labels[index]

        return img, voxel_label, mesh_label

    def _load_data3D_raw(self, filename: str):
        data = []
        for fn in self._files:
            img = nib.load(os.path.join(self._raw_data_dir, fn, filename))

            d = img.get_fdata()
            data.append(d)

        return data

    def _load_data3D(self, filename: str, is_label: bool, pad_width=5):
        """Load the image data or load a patch of each image centered at 'patch_origin' and of
        shape 'patch_shape' with each side padded with 'pad' zeros. """

        data = self._load_data3D_raw(filename)

        if not self.patch_mode:
            return data

        # Limits for patch selection
        lower_limit = np.array(self._patch_origin) + pad_width
        upper_limit = np.array(self._patch_origin) + np.array(self.select_patch_size) - pad_width

        data_patch = []

        for img in data:
            assert all(upper_limit <= img.shape), "Upper patch limit too high"
            # Select patch from whole image
            img_patch = img
            img_patch = img_patch[lower_limit[0]:upper_limit[0],
                                  lower_limit[1]:upper_limit[1],
                                  lower_limit[2]:upper_limit[2]]
            img_patch = np.pad(img_patch, pad_width)
            # Zoom to certain size
            if self.patch_size != self.select_patch_size:
                if is_label:
                    img_patch = F.interpolate(
                        torch.from_numpy(img_patch)[None][None],
                        size=self.patch_size,
                        mode='nearest',
                    ).squeeze().numpy()
                else:
                    img_patch = F.interpolate(
                        torch.from_numpy(img_patch)[None][None],
                        size=self.patch_size,
                        mode='trilinear',
                        align_corners=False
                    ).squeeze().numpy()
            data_patch.append(img_patch)

        return data_patch

    def _create_voxel_labels_from_meshes(self):
        """ Return the voxelized meshes as 3D voxel labels """
        data = []
        for m in self.mesh_labels:
            voxel_label = torch.zeros(self.patch_size, dtype=torch.long)
            vertices = m.vertices.view(self.n_m_classes, -1, 3)
            vertices = vertices.flip(dims=[2]) # convert x,y,z -> z, y, x
            faces = m.faces.view(self.n_m_classes, -1, 3)
            voxel_label = voxelize_mesh(
                vertices, faces, self.patch_size, self.n_m_classes
            )

            data.append(voxel_label.numpy())

        return data

    def _load_dataMesh(self, meshnames):
        """ Load mesh such that it's registered to the respective 3D image
        """
        data = []
        centers_per_structure = {mn: [] for mn in meshnames}
        radii_per_structure = {mn: [] for mn in meshnames}
        for fn in self._files:
            # Voxel coords
            orig = nib.load(os.path.join(self._raw_data_dir, fn,
                                         'mri.nii.gz'))
            vox2world_affine = orig.affine
            world2vox_affine = np.linalg.inv(vox2world_affine)
            file_vertices = []
            file_faces = []
            for mn in meshnames:
                mesh = trimesh.load_mesh(os.path.join(
                    self._raw_data_dir, fn, mn + ".stl"
                ))
                vertices = mesh.vertices
                # World coords
                coords = np.concatenate((vertices.T,
                                          np.ones((1, vertices.shape[0]))),
                                         axis=0)
                # World --> voxel coordinates
                new_verts = (world2vox_affine @ coords).T[:,:-1]
                # Padding offset in voxel coords
                new_verts = new_verts + offset_due_to_padding(orig.shape,
                                                              self.patch_size)
                new_verts = normalize_vertices(new_verts,
                                               torch.tensor(self.patch_size)[None])
                # Convert z,y,x --> x,y,z
                new_verts = torch.flip(new_verts, dims=[1])
                file_vertices.append(new_verts)
                file_faces.append(torch.from_numpy(mesh.faces))
                center = new_verts.mean(dim=0)
                radii = torch.sqrt(torch.sum((new_verts - center)**2, dim=1)).mean(dim=0)
                centers_per_structure[mn].append(center)
                radii_per_structure[mn].append(radii)

            # First treat as a batch of multiple meshes and then combine
            # into one mesh
            mesh_batch = Meshes(file_vertices, file_faces)
            mesh_single = Mesh(
                mesh_batch.verts_padded().float(),
                mesh_batch.faces_padded().long(),
                normals=mesh_batch.verts_normals_padded().float()
            )
            data.append(mesh_single)

        # Compute centroids and average radius per structure
        if self.__len__() > 0:
            centroids = {k: torch.mean(torch.stack(v), dim=0)
                         for k, v in centers_per_structure.items()}
            radii = {k: torch.mean(torch.stack(v), dim=0)
                     for k, v in radii_per_structure.items()}
        else:
            centroids, radii = None, None

        return data, (centroids, radii)

    def _load_mc_dataMesh(self):
        """ Create ground truth meshes from voxel labels."""
        data = []
        for vl in self.voxel_labels:
            assert tuple(vl.shape) == tuple(self.patch_size),\
                    "Voxel label should be of correct size."
            mc_mesh = create_mesh_from_voxels(
                vl, mc_step_size=self._mc_step_size,
            ).to_pytorch3d_Meshes()
            data.append(Mesh(
                mc_mesh.verts_padded(),
                mc_mesh.faces_padded(),
                mc_mesh.verts_normals_padded()
            ))

        return data