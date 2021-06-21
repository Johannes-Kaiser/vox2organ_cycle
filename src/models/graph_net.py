
""" Graph (sub-)networks """

__author__ = "Fabi Bongratz"
__email__ = "fabi.bongratz@gmail.com"

from typing import Union

import torch
import torch.nn as nn
from pytorch3d.structures import Meshes
from torch.cuda.amp import autocast

from utils.utils_voxel2mesh.unpooling import uniform_unpool
from utils.utils_voxel2meshplusplus.graph_conv import (
    GraphConvNorm,
    PTGeoConvWrapped,
    Features2FeaturesSimple,
    GraphIdLayer,
    Features2FeaturesResidual
)
from utils.utils_voxel2meshplusplus.feature_aggregation import (
    aggregate_from_indices
)
from utils.utils_voxel2mesh.file_handle import read_obj
from utils.logging import measure_time
from utils.utils_voxel2meshplusplus.custom_layers import IdLayer

class GraphDecoder(nn.Module):
    """ A graph decoder that takes a template mesh and voxel features as input.
    """
    def __init__(self,
                 batch_norm: bool,
                 mesh_template: str,
                 unpool_indices: Union[list, tuple],
                 use_adoptive_unpool: bool,
                 graph_channels: Union[list, tuple],
                 skip_channels: Union[list, tuple],
                 weighted_edges: bool,
                 GC,
                 propagate_coords: bool,
                 dim: int=3,
                 aggregate: str='trilinear',
                 n_residual_blocks: int=3,
                 n_f2f_hidden_layer: int=1,
                 aggregate_indices=((3,4), (1,2), (0,1))):
        super().__init__()

        assert (len(graph_channels) - 1 ==\
                len(aggregate_indices) ==\
                len(unpool_indices)),\
                "Graph channels, aggregation indices, and unpool indices must"\
                " match the number of mesh decoder steps."

        if weighted_edges and not propagate_coords:
            raise ValueError("Edge weighing requires propagation of vertex"
                             " coordinates to the graph convs.")

        # Number of vertex latent features (1D)
        self.latent_features_count = graph_channels
        # The initial creation of latent features from vertex coordinates
        # does not count as a decoder step
        self.num_steps = len(graph_channels) - 1
        self.aggregate_indices = aggregate_indices
        self.unpool_indices = unpool_indices
        self.use_adoptive_unpool = use_adoptive_unpool

        # Aggregation of voxel features
        self.aggregate = aggregate

        # Initial creation of latent features from coordinates
        self.graph_conv_first = GC(dim, graph_channels[0], weighted_edges=weighted_edges)

        # Graph decoder
        f2f_res_layers = [] # Residual feature to feature blocks
        f2f_connect_layers = [] # Single f2f graph convs
        f2v_layers = [] # Features to vertices

        # Whether or not to add the vertex coordinates to the features again
        # after each step
        self.propagate_coords = propagate_coords
        if propagate_coords:
            add_n = 3
        else:
            add_n = 0

        for i in range(self.num_steps):
            # Multiple sequential graph residual blocks
            indices = slice(aggregate_indices[i][0],
                            aggregate_indices[i][1] + 1)
            skip_features_count =\
                torch.sum(torch.tensor(skip_channels[indices]))
            res_blocks = [Features2FeaturesResidual(
                skip_features_count + self.latent_features_count[i] + add_n,
                self.latent_features_count[i+1],
                hidden_layer_count=n_f2f_hidden_layer,
                batch_norm=batch_norm,
                GC=GC,
                weighted_edges=weighted_edges
            )]
            for _ in range(n_residual_blocks - 1):
                res_blocks.append(Features2FeaturesResidual(
                    self.latent_features_count[i+1],
                    self.latent_features_count[i+1],
                    hidden_layer_count=n_f2f_hidden_layer,
                    batch_norm=batch_norm,
                    GC=GC,
                    weighted_edges=False # No weighted edges here
                ))

            # Cannot be nn.Sequential because graph convs take two inputs but
            # provide only one output. Maybe try torch_geometric.nn.Sequential
            res_blocks = nn.ModuleList(res_blocks)
            f2f_res_layers.append(res_blocks)

            # Feature to vertex layer, edge weighing never used
            f2v_layers.append(GC(
                self.latent_features_count[i+1], dim, weighted_edges=False
            ))

            # Feature to feature layer that connects to the next decoder step
            if i < self.num_steps - 1:
                f2f_connect_layers.append(Features2FeaturesSimple(
                    self.latent_features_count[i+1] + add_n,
                    self.latent_features_count[i+1],
                    batch_norm = batch_norm,
                    GC=GC,
                    weighted_edges=weighted_edges
                ))
            else:
                f2f_connect_layers.append(GraphIdLayer())

        self.f2f_res_layers = nn.ModuleList(f2f_res_layers)
        self.f2f_connect_layers = nn.ModuleList(f2f_connect_layers)
        self.f2v_layers = nn.ModuleList(f2v_layers)

        # Template
        sphere_path = mesh_template
        sphere_vertices, sphere_faces = read_obj(sphere_path)
        sphere_vertices = torch.from_numpy(sphere_vertices).cuda().float()
        self.sphere_vertices = sphere_vertices/torch.sqrt(torch.sum(sphere_vertices**2, dim=1)[:,None])[None]
        self.sphere_faces = torch.from_numpy(sphere_faces).cuda().long()[None]

    @property
    def unpool_indices(self):
        return self._unpool_indices

    @unpool_indices.setter
    def unpool_indices(self, indices):
        """ Set the unpool indices """
        if len(indices) != self.num_steps:
            raise ValueError("Invalid unpool indices.")
        self._unpool_indices = indices

    @property
    def use_adoptive_unpool(self):
        return self._use_adoptive_unpool

    @use_adoptive_unpool.setter
    def use_adoptive_unpool(self, value: bool):
        """ Define adoptive unpooling """
        self._use_adoptive_unpool = value

    @measure_time
    def forward(self, skips):

        # Batch of template meshes
        batch_size = skips[0].shape[0]
        temp_vertices = torch.cat(batch_size * [self.sphere_vertices], dim=0)
        temp_faces = torch.cat(batch_size * [self.sphere_faces], dim=0)
        temp_meshes = Meshes(verts=temp_vertices, faces=temp_faces)

        # Avoid bug related to automatic mixed precision, see also
        # https://github.com/pytorch/pytorch/issues/42218
        skips = [s.float() for s in skips]
        with autocast(enabled=False):
            # First graph conv: Vertex coords --> latent features
            edges_packed = temp_meshes.edges_packed()
            verts_packed = temp_meshes.verts_packed()
            latent_features = self.graph_conv_first(verts_packed, edges_packed)
            features_verts = torch.cat((latent_features, verts_packed), dim=1)
            features_verts = features_verts.view(
                batch_size, temp_vertices.shape[1], self.latent_features_count[0] + 3
            )
            temp_meshes = Meshes(verts=features_verts, faces=temp_faces)

            pred_meshes = [temp_meshes]
            # No delta V for initial step
            pred_deltaV = [None]

            # Iterate over decoder steps
            for i, (f2f_res,
                    f2f_connect,
                    f2v,
                    agg_indices,
                    do_unpool) in enumerate(zip(
                        self.f2f_res_layers,
                        self.f2f_connect_layers,
                        self.f2v_layers,
                        self.aggregate_indices,
                        self.unpool_indices)):

                # Load mesh information from previous iteration for class k
                prev_meshes = pred_meshes[i]
                vertices_padded = prev_meshes.verts_padded()[:,:,-3:] # (B,V,3)
                latent_features_padded = prev_meshes.verts_padded()[:,:,:-3] # (B,V,latent_features_count)
                faces_padded = prev_meshes.faces_padded() # (B,F,3)

                if do_unpool == 1:
                    faces_prev = faces_padded
                    _, N_prev, _ = vertices_padded.shape

                    # Get candidate vertices using uniform unpool
                    vertices_padded,\
                            faces_padded_new = uniform_unpool(vertices_padded,
                                                  faces_padded,
                                                  identical_face_batch=False)
                    latent_features_padded, _ = uniform_unpool(latent_features_padded,
                                                  faces_padded,
                                                  identical_face_batch=False)
                    faces_padded = faces_padded_new

                # Latent features of vertices from voxels
                skipped_features = aggregate_from_indices(
                    skips,
                    vertices_padded,
                    agg_indices,
                    mode=self.aggregate
                )

                features_verts_padded = torch.cat(
                    (latent_features_padded, skipped_features, vertices_padded),
                    dim=2
                )

                # New latent features
                N_new = features_verts_padded.shape[1]
                new_meshes = Meshes(features_verts_padded, faces_padded)
                edges_packed = new_meshes.edges_packed()
                if self.propagate_coords:
                    latent_features_packed = new_meshes.verts_packed()
                else:
                    latent_features_packed = new_meshes.verts_packed()[:,:-3]
                for f2f in f2f_res:
                    latent_features_packed =\
                        f2f(latent_features_packed, edges_packed)

                # Move vertices
                deltaV_packed = f2v(latent_features_packed, edges_packed)
                deltaV_padded = deltaV_packed.view(batch_size, N_new, -1)
                vertices_packed = new_meshes.verts_packed()[:,-3:]
                vertices_packed = vertices_packed + deltaV_packed

                # New latent features
                if self.propagate_coords:
                    latent_features_packed = torch.cat(
                        (latent_features_packed, vertices_packed), dim=1
                    )
                latent_features_packed = f2f_connect(latent_features_packed,
                                                     edges_packed)

                # Latent features = (latent features, vertex positions)
                features_verts_packed = torch.cat((latent_features_packed,
                                                   vertices_packed), dim=1)
                # !Requires all meshes to have the same number of vertices!
                features_verts_padded =\
                    features_verts_packed.view(batch_size, N_new, -1)

                # Final meshes
                new_meshes = Meshes(features_verts_padded,
                                    new_meshes.faces_padded())

                if do_unpool == 1 and self.use_adoptive_unpool:
                    raise NotImplementedError("Adoptive unpooling changes the"\
                                              " number of vertices for each"\
                                              " mesh which is currently"\
                                              " expected to lead to problems.")
                    # Discard the vertices that were introduced from the uniform unpool and didn't deform much
                    # vertices, faces, latent_features, temp_vertices_padded = adoptive_unpool(vertices, faces_prev, sphere_vertices, latent_features, N_prev)

                pred_meshes.append(new_meshes)
                pred_deltaV.append(deltaV_padded)

        return pred_meshes, pred_deltaV