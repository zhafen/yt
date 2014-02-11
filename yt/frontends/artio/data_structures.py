"""
ARTIO-specific data structures




"""

#-----------------------------------------------------------------------------
# Copyright (c) 2013, yt Development Team.
#
# Distributed under the terms of the Modified BSD License.
#
# The full license is in the file COPYING.txt, distributed with this software.
#-----------------------------------------------------------------------------
import numpy as np
import stat
import weakref
import cStringIO

from .definitions import yt_to_art, art_to_yt, ARTIOconstants
from _artio_caller import \
    artio_is_valid, artio_fileset, ARTIOOctreeContainer, \
    ARTIORootMeshContainer, ARTIOSFCRangeHandler
import _artio_caller
from yt.utilities.definitions import \
    mpc_conversion, sec_conversion
from .fields import \
    ARTIOFieldInfo, \
    b2t
from yt.fields.particle_fields import \
    standard_particle_fields

from yt.funcs import *
from yt.geometry.geometry_handler import \
    Index, YTDataChunk
import yt.geometry.particle_deposit as particle_deposit
from yt.data_objects.dataset import \
    Dataset
from yt.data_objects.octree_subset import \
    OctreeSubset
from yt.data_objects.data_containers import \
    YTFieldData

from yt.fields.field_info_container import \
    FieldInfoContainer, NullFunc

class ARTIOOctreeSubset(OctreeSubset):
    _domain_offset = 0
    domain_id = -1
    _con_args = ("base_region", "sfc_start", "sfc_end", "oct_handler", "pf")
    _type_name = 'octree_subset'
    _num_zones = 2

    def __init__(self, base_region, sfc_start, sfc_end, oct_handler, pf):
        self.field_data = YTFieldData()
        self.field_parameters = {}
        self.sfc_start = sfc_start
        self.sfc_end = sfc_end
        self.oct_handler = oct_handler
        self.pf = pf
        self.hierarchy = self.pf.hierarchy
        self._last_mask = None
        self._last_selector_id = None
        self._current_particle_type = 'all'
        self._current_fluid_type = self.pf.default_fluid_type
        self.base_region = base_region
        self.base_selector = base_region.selector

    @property
    def min_ind(self):
        return self.sfc_start

    @property
    def max_ind(self):
        return self.sfc_end

    def fill(self, fields, selector):
        if len(fields) == 0: return []
        handle = self.oct_handler.artio_handle
        field_indices = [handle.parameters["grid_variable_labels"].index(f)
                        for (ft, f) in fields]
        cell_count = selector.count_oct_cells(
            self.oct_handler, self.domain_id)
        self.data_size = cell_count
        levels, cell_inds, file_inds = self.oct_handler.file_index_octs(
            selector, self.domain_id, cell_count)
        domain_counts = self.oct_handler.domain_count(selector)
        tr = [np.zeros(cell_count, dtype="float64") for field in fields]
        self.oct_handler.fill_sfc(levels, cell_inds, file_inds,
            domain_counts, field_indices, tr)
        tr = dict((field, v) for field, v in zip(fields, tr))
        return tr

    def fill_particles(self, fields):
        if len(fields) == 0: return {}
        art_fields = []
        for s, f in fields:
            fn = yt_to_art[f]
            for i in self.pf.particle_type_map[s]:
                if fn in self.pf.particle_variables[i]:
                    art_fields.append((i, fn))
        species_data = self.oct_handler.fill_sfc_particles(art_fields)
        tr = defaultdict(dict)
        # Now we need to sum things up and then fill
        for s, f in fields:
            count = 0
            fn = yt_to_art[f]
            dt = "float64" # default
            for i in self.pf.particle_type_map[s]:
                if (i, fn) not in species_data: continue
                # No vector fields in ARTIO
                count += species_data[i, fn].size
                dt = species_data[i, fn].dtype
            tr[s][f] = np.zeros(count, dtype=dt)
            cp = 0
            for i in self.pf.particle_type_map[s]:
                if (i, fn) not in species_data: continue
                v = species_data.pop((i, fn))
                tr[s][f][cp:cp+v.size] = v
                cp += v.size
        return tr

# We create something of a fake octree here.  This is primarily to enable us to
# reuse code for things like __getitem__ and the like.  We will also create a
# new oct_handler type that is functionally equivalent, except that it will
# only manage the root mesh.
class ARTIORootMeshSubset(ARTIOOctreeSubset):
    _num_zones = 1
    _type_name = 'sfc_subset'
    _selector_module = _artio_caller
    domain_id = -1

    def fill(self, fields, selector):
        # We know how big these will be.
        if len(fields) == 0: return []
        handle = self.pf._handle
        field_indices = [handle.parameters["grid_variable_labels"].index(f)
                        for (ft, f) in fields]
        tr = self.oct_handler.fill_sfc(selector, field_indices)
        self.data_size = tr[0].size
        tr = dict((field, v) for field, v in zip(fields, tr))
        return tr

    def deposit(self, positions, fields = None, method = None):
        # Here we perform our particle deposition.
        if fields is None: fields = []
        cls = getattr(particle_deposit, "deposit_%s" % method, None)
        if cls is None:
            raise YTParticleDepositionNotImplemented(method)
        nz = self.nz
        nvals = (nz, nz, nz, self.ires.size)
        op = cls(nvals) # We allocate number of zones, not number of octs
        op.initialize()
        mylog.debug("Depositing %s (%s^3) particles into %s Root Mesh",
            positions.shape[0], positions.shape[0]**0.3333333, nvals[-1])
        pos = np.array(positions, dtype="float64")
        f64 = [np.array(f, dtype="float64") for f in fields]
        self.oct_handler.deposit(op, self.base_selector, pos, f64)
        vals = op.finalize()
        if vals is None: return
        return np.asfortranarray(vals)

class ARTIOIndex(Index):

    def __init__(self, pf, dataset_type='artio'):
        self.dataset_type = dataset_type
        self.parameter_file = weakref.proxy(pf)
        # for now, the hierarchy file is the parameter file!
        self.hierarchy_filename = self.parameter_file.parameter_filename
        self.directory = os.path.dirname(self.hierarchy_filename)

        self.max_level = pf.max_level
        self.float_type = np.float64
        super(ARTIOIndex, self).__init__(pf, dataset_type)

    @property
    def max_range(self):
        return self.parameter_file.max_range

    def _setup_geometry(self):
        mylog.debug("Initializing Geometry Handler empty for now.")

    def get_smallest_dx(self):
        """
        Returns (in code units) the smallest cell size in the simulation.
        """
        return  1.0/(2**self.max_level)

    def convert(self, unit):
        return self.parameter_file.conversion_factors[unit]

    def find_max(self, field, finest_levels=3):
        """
        Returns (value, center) of location of maximum for a given field.
        """
        if (field, finest_levels) in self._max_locations:
            return self._max_locations[(field, finest_levels)]
        mv, pos = self.find_max_cell_location(field, finest_levels)
        self._max_locations[(field, finest_levels)] = (mv, pos)
        return mv, pos

    def find_max_cell_location(self, field, finest_levels=3):
        source = self.all_data()
        if finest_levels is not False:
            source.min_level = self.max_level - finest_levels
        mylog.debug("Searching for maximum value of %s", field)
        max_val, maxi, mx, my, mz = \
            source.quantities["MaxLocation"](field)
        mylog.info("Max Value is %0.5e at %0.16f %0.16f %0.16f",
                   max_val, mx, my, mz)
        self.pf.parameters["Max%sValue" % (field)] = max_val
        self.pf.parameters["Max%sPos" % (field)] = "%s" % ((mx, my, mz),)
        return max_val, np.array((mx, my, mz), dtype='float64')

    def _detect_output_fields(self):
        self.fluid_field_list = self._detect_fluid_fields()
        self.particle_field_list = self._detect_particle_fields()
        self.field_list = self.fluid_field_list + self.particle_field_list
        mylog.debug("Detected fields: %s", (self.field_list,))

    def _detect_fluid_fields(self):
        return [("artio", f) for f in
                self.pf.artio_parameters["grid_variable_labels"]]

    def _detect_particle_fields(self):
        fields = set()
        for ptype in self.pf.particle_types:
            if ptype == "all": continue
            for f in yt_to_art.values():
                if all(f in self.pf.particle_variables[i]
                       for i in range(self.pf.num_species)
                       if art_to_yt[self.pf.particle_species[i]] == ptype):
                    fields.add((ptype, art_to_yt[f]))
        return list(fields)

    def _identify_base_chunk(self, dobj):
        if getattr(dobj, "_chunk_info", None) is None:
            try:
                all_data = all(dobj.left_edge == self.pf.domain_left_edge) and\
                    all(dobj.right_edge == self.pf.domain_right_edge)
            except:
                all_data = False
            base_region = getattr(dobj, "base_region", dobj)
            sfc_start = getattr(dobj, "sfc_start", None)
            sfc_end = getattr(dobj, "sfc_end", None)
            nz = getattr(dobj, "_num_zones", 0)
            if all_data:
                mylog.debug("Selecting entire artio domain")
                list_sfc_ranges = self.pf._handle.root_sfc_ranges_all(
                    max_range_size = self.max_range)
            elif sfc_start is not None and sfc_end is not None:
                mylog.debug("Restricting to %s .. %s", sfc_start, sfc_end)
                list_sfc_ranges = [(sfc_start, sfc_end)]
            else:
                mylog.debug("Running selector on artio base grid")
                list_sfc_ranges = self.pf._handle.root_sfc_ranges(
                    dobj.selector, max_range_size = self.max_range)
            ci = []
            #v = np.array(list_sfc_ranges)
            #list_sfc_ranges = [ (v.min(), v.max()) ]
            for (start, end) in list_sfc_ranges:
                range_handler = ARTIOSFCRangeHandler(
                    self.pf.domain_dimensions,
                    self.pf.domain_left_edge, self.pf.domain_right_edge,
                    self.pf._handle, start, end)
                range_handler.construct_mesh()
                if nz != 2:
                    ci.append(ARTIORootMeshSubset(base_region, start, end,
                                range_handler.root_mesh_handler, self.pf))
                if nz != 1 and range_handler.total_octs > 0:
                    ci.append(ARTIOOctreeSubset(base_region, start, end,
                      range_handler.octree_handler, self.pf))
            dobj._chunk_info = ci
            if len(list_sfc_ranges) > 1:
                mylog.info("Created %d chunks for ARTIO" % len(list_sfc_ranges))
        dobj._current_chunk = list(self._chunk_all(dobj))[0]

    def _data_size(self, dobj, dobjs):
        size = 0
        for d in dobjs:
            size += d.data_size
        return size

    def _chunk_all(self, dobj):
        oobjs = getattr(dobj._current_chunk, "objs", dobj._chunk_info)
        yield YTDataChunk(dobj, "all", oobjs, None, cache = True)

    def _chunk_spatial(self, dobj, ngz, preload_fields = None):
        if ngz > 0:
            raise NotImplementedError
        sobjs = getattr(dobj._current_chunk, "objs", dobj._chunk_info)
        for i,og in enumerate(sobjs):
            if ngz > 0:
                g = og.retrieve_ghost_zones(ngz, [], smoothed=True)
            else:
                g = og
            yield YTDataChunk(dobj, "spatial", [g], None, cache = True)

    def _chunk_io(self, dobj, cache = True):
        # _current_chunk is made from identify_base_chunk
        oobjs = getattr(dobj._current_chunk, "objs", dobj._chunk_info)
        for chunk in oobjs:
            yield YTDataChunk(dobj, "io", [chunk], None,
                              cache = cache)

    def _read_fluid_fields(self, fields, dobj, chunk=None):
        if len(fields) == 0:
            return {}, []
        if chunk is None:
            self._identify_base_chunk(dobj)
        fields_to_return = {}
        fields_to_read, fields_to_generate = self._split_fields(fields)
        if len(fields_to_read) == 0:
            return {}, fields_to_generate
        fields_to_return = self.io._read_fluid_selection(
            self._chunk_io(dobj),
            dobj.selector,
            fields_to_read)
        return fields_to_return, fields_to_generate


class ARTIODataset(Dataset):
    _handle = None
    _index_class = ARTIOIndex
    _field_info_class = ARTIOFieldInfo
    _particle_mass_name = "particle_mass"
    _particle_coordinates_name = "Coordinates"
    max_range = 1024

    def __init__(self, filename, dataset_type='artio',
                 storage_filename=None):
        if self._handle is not None:
            return
        self.fluid_types += ("artio",)
        self._filename = filename
        self._fileset_prefix = filename[:-4]
        self._handle = artio_fileset(self._fileset_prefix)
        self.artio_parameters = self._handle.parameters
        # Here we want to initiate a traceback, if the reader is not built.
        Dataset.__init__(self, filename, dataset_type)
        self.storage_filename = storage_filename

    def _set_code_unit_attributes(self):
        self.mass_unit = self.quan(self.parameters["unit_m"], "g")
        self.length_unit = self.quan(self.parameters["unit_l"], "cm")
        self.time_unit = self.quan(self.parameters["unit_t"], "s")
        self.velocity_unit = self.length_unit / self.time_unit

    def _parse_parameter_file(self):
        # hard-coded -- not provided by headers
        self.dimensionality = 3
        self.refine_by = 2
        self.parameters["HydroMethod"] = 'artio'
        self.parameters["Time"] = 1.  # default unit is 1...

        # read header
        self.unique_identifier = \
            int(os.stat(self.parameter_filename)[stat.ST_CTIME])

        self.num_grid = self._handle.num_grid
        self.domain_dimensions = np.ones(3, dtype='int32') * self.num_grid
        self.domain_left_edge = np.zeros(3, dtype="float64")
        self.domain_right_edge = np.ones(3, dtype='float64')*self.num_grid

        # TODO: detect if grid exists
        self.min_level = 0  # ART has min_level=0
        self.max_level = self.artio_parameters["max_refinement_level"][0]

        # TODO: detect if particles exist
        if self._handle.has_particles:
            self.num_species = self.artio_parameters["num_particle_species"][0]
            self.particle_variables = [["PID", "SPECIES"]
                                   for i in range(self.num_species)]
            self.particle_species = \
                self.artio_parameters["particle_species_labels"]
            self.particle_type_map = {}
            for i, s in enumerate(self.particle_species):
                f = art_to_yt[s]
                if f not in self.particle_type_map:
                    self.particle_type_map[f] = []
                self.particle_type_map[f].append(i)

            for species in range(self.num_species):
                # Mass would be best as a derived field,
                # but wouldn't detect under 'all'
                if self.artio_parameters["particle_species_labels"][species]\
                        == "N-BODY":
                    self.particle_variables[species].append("MASS")

                if self.artio_parameters["num_primary_variables"][species] > 0:
                    self.particle_variables[species].extend(
                        self.artio_parameters[
                            "species_%02d_primary_variable_labels"
                            % (species, )])
                if self.artio_parameters["num_secondary_variables"][species] > 0:
                    self.particle_variables[species].extend(
                            self.artio_parameters[
                            "species_%02d_secondary_variable_labels"
                            % (species, )])

            self.particle_types_raw = tuple(
                set(art_to_yt[s] for s in
                    self.artio_parameters["particle_species_labels"]))
            self.particle_types = tuple(self.particle_types)
        else:
            self.num_species = 0
            self.particle_variables = []
            self.particle_types = ()
        self.particle_types_raw = self.particle_types

        self.current_time = b2t(self.artio_parameters["tl"][0])

        # detect cosmology
        if "abox" in self.artio_parameters:
            abox = self.artio_parameters["abox"][0]
            self.cosmological_simulation = True
            self.omega_lambda = self.artio_parameters["OmegaL"][0]
            self.omega_matter = self.artio_parameters["OmegaM"][0]
            self.hubble_constant = self.artio_parameters["hubble"][0]
            self.current_redshift = 1.0/self.artio_parameters["abox"][0] - 1.0

            self.parameters["initial_redshift"] =\
                1.0 / self.artio_parameters["auni_init"][0] - 1.0
            self.parameters["CosmologyInitialRedshift"] =\
                self.parameters["initial_redshift"]
        else:
            self.cosmological_simulation = False

        #units
        if self.cosmological_simulation:
            self.parameters['unit_m'] = self.artio_parameters["mass_unit"][0]
            self.parameters['unit_t'] =\
                self.artio_parameters["time_unit"][0] * abox**2
            self.parameters['unit_l'] =\
                self.artio_parameters["length_unit"][0] * abox
        else:
            self.parameters['unit_l'] = self.artio_parameters["length_unit"][0]
            self.parameters['unit_t'] = self.artio_parameters["time_unit"][0]
            self.parameters['unit_m'] = self.artio_parameters["mass_unit"][0]

        # hard coded assumption of 3D periodicity (add to parameter file)
        self.periodicity = (True, True, True)

    @classmethod
    def _is_valid(self, *args, **kwargs):
        # a valid artio header file starts with a prefix and ends with .art
        if not args[0].endswith(".art"):
            return False
        return artio_is_valid(args[0][:-4])
