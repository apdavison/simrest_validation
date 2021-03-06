from networkunit.capabilities import ProducesCovariances
from networkunit.models import data_model
import os
import glob
import copy
import warnings
import numpy as np
import quantities as pq
from neo.io.blackrockio import BlackrockIO
import odml
import re

class resting_state_data(data_model, ProducesCovariances):
    """
    A model class to wrap network activity data (in form of spike trains) from
    an resting state experiment on a macaque monkey.
    """

    def load(self, file_path='./i140701-004', class_file=None, eiThres=0.4,
             t_start = None, t_stop = None, **kwargs):
        '''
        Loads nikos2 resting state data.
        For spike trains loads only those with annotation 'sua' = True.
        Returns neo matrices of spike trains and analog signals.
        '''
        session = RestingStateIO(file_path)
        block = session.read_block(n_starts = t_start, n_stops = t_stop,
                                   channels = 'all', units = 'all',
                                   nsx_to_load = 2, load_waveforms = True)
        # load only those spike trains with annotation 'sua' = True
        self.spiketrains = np.asarray([ st for st in block.segments[0].spiketrains
                           if st.annotations['sua'] ])
        if class_file:
            self._neuron_type_separation(self.spiketrains,
                                         eiThres=eiThres,
                                         class_file=class_file)
        print 'Nikos2 data loaded'
        return self.spiketrains

    def _neuron_type_separation(self, sts,
                                class_file='./nikos2rs_consistency_EIw035complexc04.txt',
                                eiThres=0.4):
        '''
        This function loads the consistencies for each unit.
        The consistencies are the percentages of single waveforms with
        trough-to-peak times (t2p) larger than 350ms.

        Single units with small/large t2p are narrow/broad spiking units
        that are putative inhibitory/excitatory units.

        The input neo SpikeTrain objects will be anotated with neu_type 'exc',
        'inh', or 'mix' if too many inconsistent waveforms are present

        INPUT:
        eiThres [0-1]: threshold for the consistency. A small value will
                       result in highly consistent waveforms. However, a
                       large amount of units will then not be classified.

        OUTPUT:
        None
        '''
        Nunits = len(sts)
        consistency = np.loadtxt(class_file,
                                 dtype=np.float16)
        exc = np.where(consistency >= 1 - eiThres)[0]
        inh = np.where(consistency <= eiThres)[0]
        mix = np.where(np.logical_and(consistency > eiThres,
                                      consistency < 1 - eiThres))[0]

        for i in exc:
            sts[i].annotations['neu_type'] = 'exc'
        for i in inh:
            sts[i].annotations['neu_type'] = 'inh'
        for i in mix:
            sts[i].annotations['neu_type'] = 'mix'

        print '\n## Classification of waveforms resulted in:'
        print '{}/{} ({:0.1f}%) neurons classified as putative excitatory'.format(
            len(exc), Nunits, float(len(exc)) / Nunits * 100.)
        print '{}/{} ({:0.1f}%) neurons classified as putative inhibitory'.format(
            len(inh), Nunits, float(len(inh)) / Nunits * 100.)
        print '{}/{} ({:0.1f}%) neurons unclassified (mixed)\n'.format(
            len(mix), Nunits, float(len(mix)) / Nunits * 100.)



class RestingStateIO(BlackrockIO):
    """
    Derived class to handle a managing a Blackrock session recording from the
    reach to grasp experiments.

    Attributes:
        txt_fileprefix (str):
            File name of the chosen .txt file (without extension).
        is_sorted (bool):
            True if a genuine sorting (i.e., a txt file) has been loaded to
            distinguish SUA and MUA.
    """

    def __init__(
            self, filename, odmldir=None, nsx_override=None, nev_override=None,
            sif_override=None, ccf_override=None, odml_filename=None,
            verbose=False):
        """
        Constructor
        """

        # Remember choice whether to print diagnostic messages or not
        self._verbose = verbose
        self.__verbose_messages = []

        if odmldir is None:
            odmldir = ''

        for ext in self.extensions:
            filename = re.sub(os.path.extsep + ext + '$', '', filename)


        sorting_version = None
        txtpostfix = None
        if nev_override:
            sorting_version = nev_override
        else:
            nev_versions = [re.sub(
                os.path.extsep + 'nev$', '', p) for p in glob.glob(
                    filename + '*.nev')]
            nev_versions = [p.replace(filename, '') for p in nev_versions]
            if nev_versions:
                sorting_version = sorted(nev_versions)[-1]

        if sorting_version:
            if os.path.isfile(r'' + filename + sorting_version + "-test.txt"):
                txtpostfix = sorting_version + '-test'
            elif os.path.isfile(r'' + filename + sorting_version + ".txt"):
                txtpostfix = sorting_version

        # Initialize file
        BlackrockIO.__init__(
            self, filename, nsx_override=nsx_override,
            nev_override=filename + sorting_version, sif_override=sif_override,
            ccf_override=ccf_override, verbose=verbose)

        # printing can be only done after initialization of BlackrockIO
        if sorting_version:
            # Output which file is used in the end
            self._print_verbose("Using nev file: " + filename + sorting_version
                                + ".nev")

        if txtpostfix:
            # Output which file is used in the end
            self._print_verbose("Using txt sorting file: " + filename +
                                   txtpostfix + ".txt")

        # remove extensions from overrides
        filen = os.path.split(self.filename)[-1]
        if odml_filename:
            self._filenames['odml'] = ''.join(
                [odmldir, os.path.sep, odml_filename])
        else:
            self._filenames['odml'] = ''.join([odmldir, os.path.sep, filen])

        file2check = ''.join([self._filenames['odml'], os.path.extsep, 'odml'])
        if os.path.exists(file2check):
            self._avail_files['odml'] = True
            self.odmldoc = odml.tools.xmlparser.load(file2check)
        else:
            self._avail_files['odml'] = False
            self.odmldoc = None




        # Determine path to sorting file (txt) if it exists
        if txtpostfix == None:
            self.txt_fileprefix = None

        else:
            self.txt_fileprefix = filename + txtpostfix


        # TODO: Put this in again!
        # Interpret file
        self.__load_suamua()


    def __load_suamua(self):
        """
        This function loads the text file associated with a sorted session and
        extracts the number of SUA, their IDs, and the channel of the MUA.

        Args:
            None.

        Returns:
            None.
        """

        self.__sua_ids = []
        self.__mua_ids = []

        # Text file that contains the mapping from 10X10 grid to electrode ID
        # along the linear index (from lower left to upper right, each row
        # starting from left, channel_i.e., Alexa's mapping):
        # 1: Blackrock ID of channel
        # 2: number of SUA
        # 3: if 0, no MUA; if >0: ID of MUA in nev file (non-sorted spike waveforms)

        if self.txt_fileprefix != None:
            filename_txt = self.txt_fileprefix + '.txt'

            # Load file
            nid_file_withNAN = np.loadtxt(filename_txt)

            # Remove all NaN entries and sort
            nid_file_noNAN = nid_file_withNAN[np.logical_not(np.isnan(nid_file_withNAN).any(axis=1))].astype(int)
            nid_file_noNAN = nid_file_noNAN[nid_file_noNAN[:, 0].argsort()]

            for channel_i in xrange(96):
                pos = np.nonzero(nid_file_noNAN[:, 0] == channel_i + 1)

                # Make sure only one line per channel exists in the txt file
                if len(pos) != 1:
                    raise IOError("MUA/SUA file " + filename_txt + " is corrupt -- multiple entries for channel " + str(channel_i + 1) + " present.")

                pos = pos[0]
                self.__sua_ids.append(range(1, nid_file_noNAN[pos, 1] + 1))
                if nid_file_noNAN[pos, 2] != 0:
                    self.__mua_ids.append(list(nid_file_noNAN[pos, 2]))
                else:
                    self.__mua_ids.append([])

                # Make sure the MUA ID does not overlap with the SUA IDs
                if nid_file_noNAN[pos, 2] in range(1, nid_file_noNAN[pos, 1] + 1):
                    raise IOError("MUA/SUA file " + filename_txt + " is corrupt -- MUA and SUA information for channel " + str(channel_i + 1) + " is corrupt.")

            self.is_sorted = True
        else:
            self.__sua_ids = [[] for _ in xrange(96)]
            self.__mua_ids = [[] for _ in xrange(96)]

            self.is_sorted = False

            self._print_verbose("No distinction between MUA and SUA - no txt file found.")



    def get_mua_ids(self, electrode):
        """
        Returns a list of MUA IDs recorded on a specific electrode.

        Args:
            channel_id (int):
                Electrode number 1-96 for which to extract neuron IDs.

        Returns:
            list
                List containing all MUA IDs of the specified electrode.
                If no sorting (i.e., no .txt file) exists, the return is [].
        """

        if electrode < 1  or electrode > 96:
            raise Exception("Invalid electrode ID specified.")

        return self.__mua_ids[electrode - 1]


    def get_sua_ids(self, electrode):
        """
        Returns a list of SUA IDs recorded on a specific electrode.

        Args:
            channel_id (int):
                Electrode number 1-96 for which to extract neuron IDs.

        Returns:
            list
                List containing all SUA IDs of the specified electrode.
                If no sorting (i.e., no .txt file) exists, the return is [].
        """

        if electrode < 1  or electrode > 96:
            raise Exception("Invalid electrode ID specified.")

        return self.__sua_ids[electrode - 1]


    def read_block(
            self, index=None, name=None, description=None, nsx_to_load='none',
            n_starts=None, n_stops=None, channels=range(1, 97), units='none',
            load_waveforms=False, load_events=False, scaling='raw',
            lazy=False, cascade=True, corrections=False):
        """
        Args:
            index (None, int):
                If not None, index of block is set to user input.
            name (None, str):
                If None, name is set to default, otherwise it is set to user
                input.
            description (None, str):
                If None, description is set to default, otherwise it is set to
                user input.
            nsx_to_load (int, list, str):
                ID(s) of nsx file(s) from which to load data, e.g., if set to
                5 only data from the ns5 file are loaded. If 'none' or empty
                list, no nsx files and therefore no analog signals are loaded.
                If 'all', data from all available nsx are loaded.
            n_starts (None, Quantity, list):
                Start times for data in each segment. Number of entries must be
                equal to length of n_stops. If None, intrinsic recording start
                times of files set are used.
            n_stops (None, Quantity, list):
                Stop times for data in each segment. Number of entries must be
                equal to length of n_starts. If None, intrinsic recording stop
                times of files set are used.
            channels (int, list, str):
                Channel id(s) from which to load data. If 'none' or empty list,
                no channels and therefore no analog signal or spiketrains are
                loaded. If 'all', all available channels are loaded.
            units (int, list, str, dict):
                ID(s) of unit(s) to load. If 'none' or empty list, no units and
                therefore no spiketrains are loaded. If 'all', all available
                units are loaded. If dict, the above can be specified
                individually for each channel (keys), e.g. {1: 5, 2: 'all'}
                loads unit 5 from channel 1 and all units from channel 2.
            load_waveforms (boolean):
                If True, waveforms are attached to all loaded spiketrains.
            load_events (boolean):
                If True, all recorded events are loaded.
            scaling (str):
                Determines whether time series of individual
                electrodes/channels are returned as AnalogSignals containing
                raw integer samples ('raw'), or scaled to arrays of floats
                representing voltage ('voltage'). Note that for file
                specification 2.1 and lower, the option 'voltage' requires a
                nev file to be present.
            lazy (bool):
                If True, only the shape of the data is loaded.
            cascade (bool or "lazy"):
                If True, only the block without children is returned.
            corrections (bool):
                If True, gap correction data are loaded from
                'corrections.txt' and spike times are shifted if for spikes
                after gap occurrence. Default: False

        Returns (neo.Block):
            Annotations:
                avail_file_set (list):
                    List of extensions of all available files for the given
                    recording.
                avail_nsx (boolean):
                    List of available nsx ids (int).
                avail_nev (boolean):
                    True if nev is available.
                avail_sif (boolean):
                    True if sif is available.
                avail_ccf (boolean):
                    True if ccf is available.
                rec_pauses (boolean):
                    True if at least one recording pause occurred.
                nb_segments (int):
                    Number of created segments after merging recording times
                    specified by user with the intrinsic ones of the file set.
        """

        if corrections:
            ### next 35 are copied from rgio.py
            #reading correction parameters from 'corrections.txt' file and saving them
            #gap_corrections = [gap_start_bin,gap_size_bins]
            #TODO: Make this more general, use time resolution from BRIO
            timestamp_res = 30000
            gap_corrections = [None,None]
            if corrections:
                try:
                    correction_file = open(os.path.dirname(__file__) + '/corrections.txt', 'r')
                    for line in correction_file:
                        if os.path.basename(self.filename) in line:
                            numbers = [int(s) for s in line.split() if s.isdigit()]
                            if len(numbers)==2:
                                gap_corrections =(
                                    numbers *
                                    np.array(1.0)*pq.CompoundUnit(
                                            '1.0/%i*s'%(timestamp_res)))
                            else:
                                warnings.warn('Wrong number of integers in corrections.txt for session %s'%os.path.basename(self.filename))
                            break
                    correction_file.close()
                except IOError:
                    warnings.warn('No file "corrections.txt" found.')

                #correcting n_starts and n_stops for gap
                # listify if necessary
                n_starts_c = copy.deepcopy(n_starts) if type(n_starts) == list \
                    else [n_starts]
                n_stops_c = copy.deepcopy(n_stops) if type(n_stops) == list \
                    else [
                    n_stops]

                # shift start and stop times to allow gap correction if gap is known
                if gap_corrections[0]!=None:
                    # for time_list in [n_starts_c,n_stops_c]:
                    #     #iterate over all n_start and n_stops
                    #     for i in range(len(time_list)):
                    #         if time_list[i]>=gap_corrections[0]:
                    #             time_list[i] += gap_corrections[1]

                    #iterate over all n_start and n_stops
                    for i in range(len(n_starts_c)):
                        if n_starts_c[i]>=gap_corrections[0] \
                                + gap_corrections[1]:
                            n_starts_c[i] += gap_corrections[1]
                                        #iterate over all n_start and n_stops
                    for i in range(len(n_stops_c)):
                        if n_stops_c[i]>=gap_corrections[0]:
                            n_stops_c[i] += gap_corrections[1]


            # Load neo block
            block = BlackrockIO.read_block(
                self, index=index, name=name, description=description,
                nsx_to_load=nsx_to_load, n_starts=n_starts_c, n_stops=n_stops_c,
                channels=channels, units=units, load_waveforms=load_waveforms,
                load_events=load_events, scaling=scaling, lazy=lazy,
                cascade=cascade)

            # Apply alignment corrections
            #post correct gaps if gap is known
            if corrections and gap_corrections[0]!=None:
                # correct alignment
                for i in range(len(block.segments)):

                    # adjust spiketrains
                    for j in range(len(block.segments[i].spiketrains)):
                        st = block.segments[i].spiketrains[j]

                        #adjusting t_start
                        if st.t_start >= gap_corrections[0] + gap_corrections[1]:
                            st.t_start -= gap_corrections[1]

                        # correct for gap
                        st = st-((st>gap_corrections[0])*gap_corrections[1])

                        # discard spikes before t_start
                        if n_starts[i]:
                            idx_valid = np.nonzero(st >= n_starts[i])[0]
                            if len(idx_valid):
                                st = st[idx_valid[0]:]

                        # discard spikes after t_stop
                        if n_stops[i]:
                            idx_invalid = np.nonzero(st >= n_stops[i])[0]
                            if len(idx_invalid):
                                st = st[:idx_invalid[0]]

                        # shallow copy from original spiketrain (annotations, waveforms, etc.)
                        st.__dict__ = block.segments[i].spiketrains[j].__dict__.copy()

                        #adjusting t_stop
                        if st.t_stop >= gap_corrections[0] + gap_corrections[1]:
                            st.t_stop -= gap_corrections[1]

                        # link block to new spiketrain
                        block.segments[i].spiketrains[j] = st

                    # adjust analogsignals
                    for j in range(len(block.segments[i].analogsignals)):
                        # discard data after t_stop
                        if n_stops[i]:
                            idx_invalid = np.nonzero(block.segments[i].analogsignals[j].times >= n_stops[i])[0]
                            if len(idx_invalid):
                                block.segments[i].analogsignals[j] = block.segments[i].analogsignals[j][:idx_invalid[0]]

        else:
            # Load neo block
            block = BlackrockIO.read_block(
                self, index=index, name=name, description=description,
                nsx_to_load=nsx_to_load, n_starts=n_starts, n_stops=n_stops,
                channels=channels, units=units, load_waveforms=load_waveforms,
                load_events=load_events, scaling=scaling, lazy=lazy,
                cascade=cascade)

        # Annotate corrections to block
        block.annotate(corrected=corrections)

        monkey_prefix = os.path.basename(self.filename)[0]
        # Annotate Block with electrode id list for connector alignment
        if monkey_prefix in 'si':
            block.annotate(elid_list_ca=[-1, 81, 83, 85, 88, 90, 92, 93, 96, -1,
                                         79, 80, 84, 86, 87, 89, 91, 94, 63, 95,
                                         77, 78, 82, 49, 53, 55, 57, 59, 61, 32,
                                         75, 76, 45, 47, 51, 56, 58, 60, 64, 30,
                                         73, 74, 41, 43, 44, 46, 52, 62, 31, 28,
                                         71, 72, 39, 40, 42, 50, 54, 21, 29, 26,
                                         69, 70, 37, 38, 48, 15, 19, 25, 27, 24,
                                         67, 68, 35, 36, 5, 17, 13, 23, 20, 22,
                                         65, 66, 33, 34, 7, 9, 11, 12, 16, 18,
                                         - 1, 2, 1, 3, 4, 6, 8, 10, 14, -1])
        else:
            self._print_verbose('No connector aligned electrode IDs available '
                                'for monkey %s'%monkey_prefix)

        # Add annotations to analogsignals and spiketrains in block
        if 'elid_list_ca' in block.annotations:
            for seg in block.segments:
                # Add annotations to analog signals and spiketrains
                for sig in seg.analogsignals:
                    if sig.annotations['channel_id'] <= 100:
                        el_id = sig.annotations['channel_id']
                        sig.annotations['el_id'] = el_id
                        ca_id =block.annotations['elid_list_ca'].index(el_id) + 1
                        sig.annotations['ca_id'] = ca_id
                for st in seg.spiketrains:
                    if st.annotations['channel_id'] <= 100:
                        el_id = st.annotations['channel_id']
                        st.annotations['el_id'] = el_id
                        ca_id = block.annotations['elid_list_ca'].index(el_id) + 1
                        st.annotations['ca_id'] = ca_id
                        if st.annotations['unit_id'] in self.get_sua_ids(el_id):
                            st.annotations['sua'] = True
                        else:
                            st.annotations['sua'] = False
            for unit in block.list_units:
                if unit.annotations['channel_id'] <= 100:
                    el_id = unit.annotations['channel_id']
                    unit.annotations['el_id'] = el_id
                    ca_id = block.annotations['elid_list_ca'].index(el_id) + 1
                    unit.annotations['ca_id'] = ca_id
                    if unit.annotations['unit_id'] in self.get_sua_ids(el_id):
                        unit.annotations['sua'] = True
                    else:
                        unit.annotations['sua'] = False
        return block