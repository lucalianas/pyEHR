import time, sys, argparse
from random import randint
from functools import wraps
import numpy as np
import itertools as it

from pyehr.ehr.services.dbmanager.dbservices.wrappers import ClinicalRecord,\
    PatientRecord, ArchetypeInstance
from pyehr.ehr.services.dbmanager.dbservices import DBServices
from pyehr.ehr.services.dbmanager.querymanager import QueryManager
from pyehr.utils.services import get_service_configuration, get_logger

import archetype_builder
from archetype_builder import Composition


class QueryPerformanceTest(object):

    def __init__(self, pyehr_conf_file, archetypes_dir, log_file=None, log_level='INFO'):
        sconf = get_service_configuration(pyehr_conf_file)
        self.db_service = DBServices(**sconf.get_db_configuration())
        self.db_service.set_index_service(**sconf.get_index_configuration())
        self.query_manager = QueryManager(**sconf.get_db_configuration())
        self.query_manager.set_index_service(**sconf.get_index_configuration())
        self.logger = get_logger('query_performance_test',
                                 log_file=log_file, log_level=log_level)
        self.archetypes_dir = archetypes_dir

    def get_execution_time(f):
        @wraps(f)
        def wrapper(inst, *args, **kwargs):
            start_time = time.time()
            res = f(inst, *args, **kwargs)
            execution_time = time.time() - start_time
            inst.logger.info('Execution of \'%s\' took %f seconds' % (f.func_name, execution_time))
            return res, execution_time
        return wrapper

    def _build_record(self, max_width, height):

        def _get_random_builder(_builders):
            builder_idx = randint(0, len(_builders)-1)
            cls = archetype_builder.get_builder( _builders[builder_idx] )
            return cls

        if height < 1:
            raise ValueError('Height must be greater than 0')

        builders = archetype_builder.BUILDERS.keys()

        if height == 1: # if height is zero it creates a leaf archetype (i.e an Observation)
            # deletes the composition from the possible builders
            leaf_builders = [b for b in builders if b != 'composition']
            children = []
            for i in xrange(max_width):
                cls = _get_random_builder(leaf_builders)
                arch = ArchetypeInstance( *cls(self.archetypes_dir).build() )
                children.append(arch)

        else:
            width = randint(1, max_width)
            arch = self._build_record(width, height - 1)
            children = [arch]

            # creates the other children. They can be Composition or Observation
            for i in xrange(max_width - 1):
                cls = _get_random_builder(builders)
                if cls == Composition:
                    width = randint(1, max_width)
                    arch = self._build_record(width, height - 1)
                else:
                    arch = ArchetypeInstance( *cls(self.archetypes_dir).build() )
                children.append(arch)

        return ArchetypeInstance(*Composition(self.archetypes_dir, children).build())

    @get_execution_time
    def build_dataset(self, patients, ehrs):
        for x in xrange(0, patients):
            crecs = list()
            p = self.db_service.save_patient(PatientRecord('PATIENT_%05d' % x))
            self.logger.debug('Saved patient PATIENT_%05d', x)
            for max_depth, max_width in it.izip([int(i) for i in np.random.normal(6, 1, ehrs)],
                                                [int(i) for i in np.random.uniform(1, 10, ehrs)]):
                if max_depth < 1:
                    max_depth = 1
                if max_depth > 11:
                    max_depth = 11
                arch = self._build_record(max_depth, max_width)
                crecs.append(ClinicalRecord(arch))
            self.logger.debug('Done building EHR %d records', ehrs)
            self.db_service.save_ehr_records(crecs, p)
            self.logger.debug('EHRs saved')
        drf = self.db_service._get_drivers_factory(self.db_service.ehr_repository)
        with drf.get_driver() as driver:
            self.logger.info('*** Produced %d different structures ***',
                             len(driver.collection.distinct('ehr_structure_id')))

    def execute_query(self, query, params=None):
        results = self.query_manager.execute_aql_query(query, params)
        self.logger.info('Retrieved %d records' % results.total_results)

    @get_execution_time
    def execute_select_all_query(self):
        query = """
        SELECT e/ehr_id/value AS patient_identifier
        FROM Ehr e
        CONTAINS Observation o[openEHR-EHR-OBSERVATION.blood_pressure.v1]
        """
        return self.execute_query(query)

    @get_execution_time
    def execute_select_all_patient_query(self, patient_index=0):
        query = """
        SELECT o/data[at0001]/events[at0006]/data[at0003]/items[at0004]/value/magnitude AS systolic,
        o/data[at0001]/events[at0006]/data[at0003]/items[at0005]/value/magnitude
        FROM Ehr e [uid=$ehrUid]
        CONTAINS Observation o[openEHR-EHR-OBSERVATION.blood_pressure.v1]
        """
        return self.execute_query(query, {'ehrUid': 'PATIENT_%05d' % patient_index})

    @get_execution_time
    def execute_filtered_query(self):
        query = """
        SELECT o/data[at0001]/events[at0006]/data[at0003]/items[at0004]/value/magnitude,
        o/data[at0001]/events[at0006]/data[at0003]/items[at0005]/value/magnitude
        FROM Ehr e
        CONTAINS Observation o[openEHR-EHR-OBSERVATION.blood_pressure.v1]
        WHERE o/data[at0001]/events[at0006]/data[at0003]/items[at0004]/value/magnitude >= 180
        OR o/data[at0001]/events[at0006]/data[at0003]/items[at0005]/value/magnitude >= 110
        """
        return self.execute_query(query)

    @get_execution_time
    def execute_patient_filtered_query(self, patient_index=0):
        query = """
        SELECT o/data[at0001]/events[at0006]/data[at0003]/items[at0004]/value/magnitude AS systolic,
        o/data[at0001]/events[at0006]/data[at0003]/items[at0005]/value/magnitude
        FROM Ehr e [uid=$ehrUid]
        CONTAINS Observation o[openEHR-EHR-OBSERVATION.blood_pressure.v1]
        WHERE o/data[at0001]/events[at0006]/data[at0003]/items[at0004]/value/magnitude >= 180
        OR o/data[at0001]/events[at0006]/data[at0003]/items[at0005]/value/magnitude >= 110
        """
        return self.execute_query(query, {'ehrUid': 'PATIENT_%05d' % patient_index})

    @get_execution_time
    def execute_patient_count_query(self):
        query = """
        SELECT e/ehr_id/value AS patient_identifier
        FROM Ehr e
        CONTAINS Observation o[openEHR-EHR-OBSERVATION.blood_pressure.v1]
        WHERE o/data[at0001]/events[at0006]/data[at0003]/items[at0004]/value/magnitude >= 180
        OR o/data[at0001]/events[at0006]/data[at0003]/items[at0005]/value/magnitude >= 110
        """
        return self.execute_query(query)

    @get_execution_time
    def cleanup(self):
        drf = self.db_service._get_drivers_factory(self.db_service.ehr_repository)
        with drf.get_driver() as driver:
            driver.collection.remove()
            driver.select_collection(self.db_service.patients_repository)
            driver.collection.remove()
        self.db_service.index_service.connect()
        self.db_service.index_service.session.execute('drop database %s' %
                                                      self.db_service.index_service.db)
        self.db_service.index_service.disconnect()

    def run(self, patients_size, ehr_size):
        self.logger.info('Creating dataset. %d patients and %d EHRs for patient' % (patients_size,
                                                                                    ehr_size))
        try:
            _, build_dataset_time = self.build_dataset(patients_size, ehr_size)
            self.logger.info('Running "SELECT ALL" query')
            _, select_all_time = self.execute_select_all_query()
            self.logger.info('Running "SELECT ALL" filtered by patient query')
            _, select_all_patient_time = self.execute_select_all_patient_query()
            self.logger.info('Running filtered query')
            _, filtered_query_time = self.execute_filtered_query()
            self.logger.info('Running filtered with patient filter')
            _, filtered_patient_time = self.execute_patient_filtered_query()
            self.logger.info('Running patient_count_query')
            _, patient_count_time = self.execute_patient_count_query()
        except Exception, e:
            self.logger.critical('AN ERROR HAS OCCURRED: %s' % e)
            raise e
        finally:
            self.logger.info('Running DB cleanup')
            _, cleanup_time = self.cleanup()
        return build_dataset_time, select_all_time, select_all_patient_time, filtered_query_time,\
               filtered_patient_time, patient_count_time, cleanup_time


def get_parser():
    parser = argparse.ArgumentParser('Run the test_query_performance tool')
    parser.add_argument('--conf-file', type=str, required=True,
                        help='pyEHR configuration file')
    parser.add_argument('--patients-size', type=int, default=10,
                        help='The number of PatientRecords that will be created for the test')
    parser.add_argument('--ehrs-size', type=int, default=10,
                        help='The number of EHR records that will be created for each patient')
    parser.add_argument('--log-file', type=str, help='LOG file (default stderr)')
    parser.add_argument('--log-level', type=str, default='INFO',
                        help='LOG level (default INFO)')
    parser.add_argument('--archetype-dir', type=str, required=True,
                        help='The directory containing archetype in json format')
    return parser


def main(argv):
    parser = get_parser()
    args = parser.parse_args(argv)
    qpt = QueryPerformanceTest(args.conf_file, args.archetype_dir, args.log_file, args.log_level)
    qpt.logger.info('--- STARTING TESTS ---')
    qpt.run(args.patients_size, args.ehrs_size)
    qpt.logger.info('--- DONE WITH TESTS ---')


if __name__ == '__main__':
    main(sys.argv[1:])