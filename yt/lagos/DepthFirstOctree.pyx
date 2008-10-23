"""
This is a recursive function to return a depth-first octree

Author: Matthew Turk <matthewturk@gmail.com>
Affiliation: KIPAC/SLAC/Stanford
Homepage: http://yt.enzotools.org/
License:
  Copyright (C) 2008 Matthew Turk.  All Rights Reserved.

  This file is part of yt.

  yt is free software; you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation; either version 3 of the License, or
  (at your option) any later version.

  This program is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU General Public License for more details.

  You should have received a copy of the GNU General Public License
  along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""

import numpy as np
cimport numpy as np
cimport cython

cdef class position:
    cdef public int output_pos, refined_pos
    def __cinit__(self):
        self.output_pos = 0
        self.refined_pos = 0

cdef class OctreeGrid:
    cdef public object child_indices, fields, left_edges, dimensions, dx
    cdef public int level
    def __cinit__(self,
                  np.ndarray[np.int32_t, ndim=3] child_indices,
                  np.ndarray[np.float64_t, ndim=4] fields,
                  np.ndarray[np.float64_t, ndim=1] left_edges,
                  np.ndarray[np.int32_t, ndim=1] dimensions,
                  np.ndarray[np.float64_t, ndim=1] dx,
                  int level):
        self.child_indices = child_indices
        self.fields = fields
        self.left_edges = left_edges
        self.dimensions = dimensions
        self.dx = dx
        self.level = level

cdef class OctreeGridList:
    cdef public object grids
    def __cinit__(self, grids):
        self.grids = grids

    def __getitem__(self, int item):
        return self.grids[item]

@cython.boundscheck(False)
def RecurseOctreeDepthFirst(int i_i, int j_i, int k_i,
                            int i_f, int j_f, int k_f,
                            position curpos, int gi, 
                            np.ndarray[np.float64_t, ndim=2] output,
                            np.ndarray[np.int32_t, ndim=1] refined,
                            OctreeGridList grids):
    cdef int i, i_off, j, j_off, k, k_off, ci, fi
    cdef int child_i, child_j, child_k
    cdef OctreeGrid child_grid
    cdef OctreeGrid grid = grids[gi-1]
    cdef np.ndarray[np.int32_t, ndim=3] child_indices = grid.child_indices
    cdef np.ndarray[np.int32_t, ndim=1] dimensions = grid.dimensions
    cdef np.ndarray[np.float64_t, ndim=4] fields = grid.fields
    cdef np.ndarray[np.float64_t, ndim=1] leftedges = grid.left_edges
    cdef np.float64_t dx = grid.dx[0]
    cdef np.float64_t child_dx
    cdef np.ndarray[np.float64_t, ndim=1] child_leftedges
    cdef np.float64_t cx, cy, cz
    for k_off in range(k_f):
        k = k_off + k_i
        cz = (leftedges[2] + k*dx)
        for j_off in range(j_f):
            j = j_off + j_i
            cy = (leftedges[1] + j*dx)
            for i_off in range(i_f):
                i = i_off + i_i
                cx = (leftedges[0] + i*dx)
                ci = grid.child_indices[i,j,k]
                if ci == -1:
                    for fi in range(fields.shape[0]):
                        output[curpos.output_pos,fi] = fields[fi,i,j,k]
                    refined[curpos.refined_pos] = 0
                    curpos.output_pos += 1
                    curpos.refined_pos += 1
                else:
                    refined[curpos.refined_pos] = 1
                    curpos.refined_pos += 1
                    child_grid = grids[ci-1]
                    child_dx = child_grid.dx[0]
                    child_leftedges = child_grid.left_edges
                    child_i = int(cx - child_leftedges[0])/child_dx
                    child_j = int(cy - child_leftedges[1])/child_dx
                    child_k = int(cz - child_leftedges[2])/child_dx
                    s = RecurseOctreeDepthFirst(child_i, child_j, child_k, 2, 2, 2,
                                        curpos, ci, output, refined, grids)
    return s

@cython.boundscheck(False)
def RecurseOctreeByLevels(int i_i, int j_i, int k_i,
                          int i_f, int j_f, int k_f,
                          np.ndarray[np.int32_t, ndim=1] curpos,
                          int gi, 
                          np.ndarray[np.float64_t, ndim=2] output,
                          np.ndarray[np.int32_t, ndim=2] genealogy,
                          np.ndarray[np.float64_t, ndim=2] corners,
                          OctreeGridList grids):
    cdef np.int32_t i, i_off, j, j_off, k, k_off, ci, fi
    cdef int child_i, child_j, child_k
    cdef OctreeGrid child_grid
    cdef OctreeGrid grid = grids[gi-1]
    cdef int level = grid.level
    cdef np.ndarray[np.int32_t, ndim=3] child_indices = grid.child_indices
    cdef np.ndarray[np.int32_t, ndim=1] dimensions = grid.dimensions
    cdef np.ndarray[np.float64_t, ndim=4] fields = grid.fields
    cdef np.ndarray[np.float64_t, ndim=1] leftedges = grid.left_edges
    cdef np.float64_t dx = grid.dx[0]
    cdef np.float64_t child_dx
    cdef np.ndarray[np.float64_t, ndim=1] child_leftedges
    cdef np.float64_t cx, cy, cz
    cdef int cp
    for k_off in range(k_f):
        k = k_off + k_i
        cz = (leftedges[2] + k*dx)
        if i_f > 2: print k, cz
        for j_off in range(j_f):
            j = j_off + j_i
            cy = (leftedges[1] + j*dx)
            for i_off in range(i_f):
                i = i_off + i_i
                cp = curpos[level]
                cx = (leftedges[0] + i*dx)
                corners[cp, 0] = cx 
                corners[cp, 1] = cy 
                corners[cp, 2] = cz
                genealogy[curpos[level], 2] = level
                # always output data
                for fi in range(fields.shape[0]):
                    output[cp,fi] = fields[fi,i,j,k]
                ci = child_indices[i,j,k]
                if ci > -1:
                    child_grid = grids[ci-1]
                    child_dx = child_grid.dx[0]
                    child_leftedges = child_grid.left_edges
                    child_i = int((cx-child_leftedges[0])/child_dx)
                    child_j = int((cy-child_leftedges[1])/child_dx)
                    child_k = int((cz-child_leftedges[2])/child_dx)
                    # set current child id to id of next cell to examine
                    genealogy[cp, 0] = curpos[level+1] 
                    # set next parent id to id of current cell
                    genealogy[curpos[level+1]:curpos[level+1]+8, 1] = cp
                    s = RecurseOctreeByLevels(child_i, child_j, child_k, 2, 2, 2,
                                              curpos, ci, output, genealogy,
                                              corners, grids)
                curpos[level] += 1
    return s
