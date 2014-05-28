from pyehr.aql.parser import *
from pyehr.ehr.services.dbmanager.drivers.interface import DriverInterface
from pyehr.ehr.services.dbmanager.querymanager.query import *
from pyehr.ehr.services.dbmanager.errors import *
from pyehr.utils import *
import elasticsearch
import time
import sys


class ElasticSearchDriver(DriverInterface):
    """
    Creates a driver to handle I\O with a ElasticSearch server.
    Parameters:
    hosts - list of nodes we should connect to.
     Node should be a dictionary ({"host": "localhost", "port": 9200}), the entire dictionary
     will be passed to the Connection class as kwargs, or a string in the format of host[:port]
     which will be translated to a dictionary automatically. If no value is given the Connection class defaults will be used.
    transport_class - Transport subclass to use.
    kwargs - any additional arguments will be passed on to the Transport class and, subsequently, to the Connection instances.

    Using the given *host:port* dictionary and, if needed, the database (index in elasticsearch terminology) and the collection
    ( document in elasticsearch terminology)  the driver will contact ES when a connection is needed and will interrogate a
    specific *document* type stored in one *index*  within the server. If no *logger* object is passed to constructor, a new one
    is created.
    *port*, *user* and *password* are not used
    """

    # This map is used to encode\decode data when writing\reading to\from ElasticSearch
    #ENCODINGS_MAP = {'.': '-'}   I NEED TO SEE THE QUERIES
    ENCODINGS_MAP = {}

    def __init__(self, host, database,collection,
                 port=elasticsearch.Urllib3HttpConnection, user=None, passwd=None,
                 index_service=None, logger=None):
        self.client = None
        self.host = host
        #usare port per transportclass ?   self.transportclass
        self.transportclass=port
        #cosa usare per parametri opzionali???? self.others
        self.user = user
        self.passwd = passwd

        # self.client = None
        # self.database = None
        # self.collection = None
        # self.host = host
        #self.database_name = database
        self.database = database
        self.collection = collection
        #self.collection_name = collection
        # self.port = port
        # self.user = user
        # self.passwd = passwd
        self.index_service = index_service
        self.logger = logger or get_logger('elasticsearch-db-driver')

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exception_type, exception_value, traceback):
        self.disconnect()
        return None

    def connect(self):
        """
        Open a connection to a ES server.
        """
        if not self.client:
            self.logger.debug('connecting to host %s', self.host)
            try:
                self.client = elasticsearch.Elasticsearch(self.host,connection_class=self.transportclass)
                self.client.info()
            except elasticsearch.TransportError:
                raise DBManagerNotConnectedError('Unable to connect to ElasticSearch at %s:%s' %
                                                (self.host[0]['host'], self.host[0]['port']))
            #self.logger.debug('binding to database %s', self.database_name)
            self.logger.debug('binding to database %s', self.database)
            #self.database = self.client[self.database_name]
            #there is no authentication/authorization layer in elasticsearch
            #if self.user:
            #    self.logger.debug('authenticating with username %s', self.user)
            #    self.database.authenticate(self.user, self.passwd)
            self.logger.debug('using collection %s', self.collection)
            #self.collection = self.database[self.collection_name]
        else:
            #self.logger.debug('Alredy connected to database %s, using collection %s',
            #                  self.database_name, self.collection_name)
            self.logger.debug('Alredy connected to ElasticSearch')

    def disconnect(self):
        """
        Close a connection to a ElasticSearch server.
        There's not such thing so we erase the client pointer
        """
        self.logger.debug('disconnecting from host %s', self.host)
        #self.client.disconnect()
        self.database = None
        self.collection = None
        self.client = None

    def init_structure(self, structure_def):
        # ElasticSearch would benefit from structure initialization but it doesn't need it
        pass

    @property
    def is_connected(self):
        """
        Check if the connection to the ElasticSearch server is opened.

        :rtype: boolean
        :return: True if the connection is open, False if it is closed.
        """
        return not self.client is None

    def __check_connection(self):
        if not self.is_connected:
            raise DBManagerNotConnectedError('Connection to host %s is closed' % self.host)

    def select_collection(self, collection_label):
        """
        Change the collection for the current database

        :param collection_label: the label of the collection that must be selected
        :type collection_label: string

        """
        self.__check_connection()
        self.logger.debug('Changing collection for database %s, old collection: %s - new collection %s',
                          self.database, self.collection, collection_label)
        self.collection = collection_label

    def _encode_patient_record(self, patient_record):
        encoded_record = {
            'creation_time': patient_record.creation_time,
            'last_update': patient_record.last_update,
            'active': patient_record.active,
            'ehr_records': [str(ehr.record_id) for ehr in patient_record.ehr_records]
        }
        if patient_record.record_id:   #it's always true
            encoded_record['_id'] = patient_record.record_id
        return encoded_record

    def _encode_clinical_record(self, clinical_record):
        def normalize_keys(document, original, encoded):
            normalized_doc = {}
            for k, v in document.iteritems():
                k = k.replace(original, encoded)
                if not isinstance(v, dict):
                    normalized_doc[k] = v
                else:
                    normalized_doc[k] = normalize_keys(v, original, encoded)
            return normalized_doc

        ehr_data = clinical_record.ehr_data
        for original_value, encoded_value in self.ENCODINGS_MAP.iteritems():
            ehr_data = normalize_keys(ehr_data, original_value, encoded_value)
        encoded_record = {
            'creation_time': clinical_record.creation_time,
            'last_update': clinical_record.last_update,
            'active': clinical_record.active,
            'archetype': clinical_record.archetype,
            'ehr_data': ehr_data,
            '_id' : clinical_record.record_id
        }
#        if clinical_record.record_id: #always true
#            encoded_record['_id'] = clinical_record.record_id
        return encoded_record

    def encode_record(self, record):
        """
        Encode a :class:`Record` object into a data structure that can be saved within
        ElasticSearch

        :param record: the record that must be encoded
        :type record: a :class:`Record` subclass
        :return: the record encoded as a ElasticSearch document
        """
        from pyehr.ehr.services.dbmanager.dbservices.wrappers import PatientRecord, ClinicalRecord

        if isinstance(record, PatientRecord):
            return self._encode_patient_record(record)
        elif isinstance(record, ClinicalRecord):
            return self._encode_clinical_record(record)
        else:
            raise InvalidRecordTypeError('Unable to map record %r' % record)

    def _decode_patient_record(self, record, loaded):
        from pyehr.ehr.services.dbmanager.dbservices.wrappers import PatientRecord

        record = decode_dict(record)
        if loaded:
            # by default, clinical records are attached as "unloaded"
            ehr_records = [self._decode_clinical_record({'_id': ehr}, loaded=False)
                           for ehr in record['ehr_records']]
            return PatientRecord(
                ehr_records=ehr_records,
                creation_time=record['creation_time'],
                last_update=record['last_update'],
                active=record['active'],
                record_id=str(record.get('_id')),
            )
        else:
            return PatientRecord(
                creation_time=record['creation_time'],
                record_id=str(record.get('_id'))
            )

    def _decode_clinical_record(self, record, loaded):
        from pyehr.ehr.services.dbmanager.dbservices.wrappers import ClinicalRecord

        def decode_keys(document, encoded, original):
            normalized_doc = {}
            for k, v in document.iteritems():
                k = k.replace(encoded, original)
                if not isinstance(v, dict):
                    normalized_doc[k] = v
                else:
                    normalized_doc[k] = decode_keys(v, encoded, original)
            return normalized_doc

        record = decode_dict(record)
        if loaded:
            ehr_data = record['ehr_data']
            for original_value, encoded_value in self.ENCODINGS_MAP.iteritems():
                ehr_data = decode_keys(ehr_data, encoded_value, original_value)
            return ClinicalRecord(
                archetype=record['archetype'],
                ehr_data=ehr_data,
                creation_time=record['creation_time'],
                last_update=record['last_update'],
                active=record['active'],
                record_id=str(record.get('_id'))
            )
        else:
            return ClinicalRecord(
                creation_time=record.get('creation_time'),
                record_id=str(record.get('_id')),
                archetype=record.get('archetype'),
                ehr_data={}
            )

    def decode_record(self, record, loaded=True):
        """
        Create a :class:`Record` object from data retrieved from ElasticSearch

        :param record: the ElasticSearch record that must be decoded
        :type record: a ElasticSearch dictionary
        :param loaded: if True, return a :class:`Record` with all values, if False all fields with
          the exception of the record_id one will have a None value
        :type loaded: boolean
        :return: the ElasticSearch document encoded as a :class:`Record` object
        """
        if 'archetype' in record:
            return self._decode_clinical_record(record, loaded)
        else:
            return self._decode_patient_record(record, loaded)

    def count(self):
        return self.client.count(index=self.database,doc_type=self.collection)['count']

    def count2(self):
        return self.client.search(index=self.database,doc_type=self.collection)['hits']['total']

    def count3(self):
        return self.client.search(index=self.database)['hits']['total']

    def add_record(self, record):
        """
        Save a record within ElasticSearch and return the record's ID

        :param record: the record that is going to be saved
        :type record: dictionary
        :return: the ID of the record
        """
        self.__check_connection()
        try:
            if(record.has_key('_id')):
                return str(self.client.index(index=self.database,doc_type=self.collection,id=record['_id'],body=record,op_type='create',refresh='true')['_id'])
#                print pippo
#                return ObjectId(str(pippo))
#                return ObjectId(self.client.index(index=self.database,doc_type=self.collection,id=record['_id'],body=record,op_type='create',refresh='true')['_id'])
            else:
                return str(self.client.index(index=self.database,doc_type=self.collection,body=record,op_type='create',refresh='true')['_id'])
        except elasticsearch.ConflictError:
            raise DuplicatedKeyError('A record with ID %s already exists' % record['_id'])


    def pack_record(self,records):
        first="{\"create\":{\"_index\":\""+self.database+"\",\"_type\":\""+self.collection+"\""
        puzzle=""
        for dox in records:
            puzzle=puzzle+first
            if(dox.has_key("_id")):
                puzzle = puzzle+",\"_id\":\""+dox["_id"]+"\"}}\n{"
            else:
                puzzle=puzzle+"}}\n{"
            for k in dox:
                puzzle=puzzle+"\""+k+"\":\""+str(dox[k])+"\","
            puzzle=puzzle.strip(",")+"}\n"
        return puzzle

    def add_records(self, records):
        """
        Save a list of records within ElasticSearch and return records' IDs

        :param records: the list of records that is going to be saved
        :type record: list
        :return: a list of records' IDs
        :rtype: list
        """
        self.__check_connection()
        #create a bulk list
        bulklist = self.pack_record(records)
        bulkanswer = self.client.bulk(body=bulklist,index=self.database,doc_type=self.collection,refresh='true')
        if(bulkanswer['errors']): # there are errors
            #count the errors
            nerrors=0
            err=[]
            errtype=[]
            for b in bulkanswer['items']:
                if(b['create'].has_key('error')):
                    err[nerrors] = b['create']['_id']
                    errtype[nerrors] = b['create']['error']
                    nerrors += 1
            if(nerrors):
                raise DuplicatedKeyError('Record with these id already exist: %s' %err)
            else:
                print 'bad programmer'
                sys.exit(1)
        else:
            return [str(g['create']['_id']) for g in bulkanswer['items']]

    def add_records2(self, records):
        """
        Save a list of records within ElasticSearch and return records' IDs

        :param records: the list of records that is going to be saved
        :type record: list
        :return: a list of records' IDs
        :rtype: list
        """
        self.__check_connection()
        return super(ElasticSearchDriver, self).add_records(records)

    def get_record_by_id(self, record_id):
        """
        Retrieve a record using its ID

        :param record_id: the ID of the record
        :return: the record of None if no match was found for the given record
        :rtype: dictionary or None

        """
        self.__check_connection()
        #res = self.client.get(index=self.database,id=record_id,_source='true')
        try:
            res = self.client.get_source(index=self.database,id=str(record_id))
            return decode_dict(res)
        except elasticsearch.NotFoundError:
            return None

    def get_all_records(self):
        """
        Retrieve all records within current collection

        :return: all the records stored in the current collection
        :rtype: list
        """
        self.__check_connection()
        restot = self.client.search(index=self.database,doc_type=self.collection)['hits']['hits']
        res = [p['_source'] for p in restot]
        if res != []:
            return ( decode_dict(res[i]) for i in range(0,len(res)) )
        return None

    def get_records_by_value(self, field, value):
        """
        Retrieve all records whose field *field* matches the given value

        :param field: the field used for the selection
        :type field: string
        :param value: the value that must be matched for the given field
        :return: a list of records
        :rtype: list
        """
        myquery = {
            "query" : {
                    "term" : { field : value }
                    }
                }
        restot = self.client.search(index=self.database,doc_type=self.collection,body=myquery)['hits']['hits']
        res = [p['_source'] for p in restot]
        if res != []:
            return ( decode_dict(res[i]) for i in range(0,len(res)) )
        return None

    def delete_record(self, record_id):
        """
        Delete an existing record

        :param record_id: record's ID
        """
        self.__check_connection()
        self.logger.debug('deleting document with ID %s', record_id)
        try:
            res=self.client.delete(index=self.database,doc_type=self.collection,id=record_id,refresh='true')
            return res
        except elasticsearch.NotFoundError:
            return None


    def update_field(self, record_id, field_label, field_value, update_timestamp_label=None):
        """
        Update record's field *field* with given value

        :param record_id: record's ID
        :param field_label: field's label
        :type field_label: string
        :param field_value: new value for the selected field
        :param update_timestamp_label: the label of the *last_update* field of the record if the last update timestamp
          must be recorded or None
          For ElasticSearch the default is not storing the timestamp
        :type update_timestamp_label: field label or None
        :return: the timestamp of the last update as saved in the DB or None (if update_timestamp_field was None)
        """
        record_to_update = self.get_record_by_id(record_id)
        if(record_to_update == None):
            self.logger.debug('No record found with ID %r', record_id)
            return None
        else:
            record_to_update[field_label]= field_value
            if update_timestamp_label:
                last_update = time.time()
                record_to_update['last_update']=last_update
            else:
                last_update=None
            res = self.client.index(index=self.database,doc_type=self.collection,body=record_to_update,id=record_id)
            self.logger.debug('updated %s document', res[u'_id'])
            return last_update

    def add_to_list(self, record_id, list_label, item_value, update_timestamp_label=None):
        """
        Append a value to a list within a document

        :param record_id: record's ID
        :param list_label: the label of the field containing the list
        :type list_label: string
        :param item_value: the item that will be appended to the list
        :param update_timestamp_label: the label of the *last_update* field of the record if the last update timestamp
          must be recorded or None
        :type update_timestamp_label: field label or None
        :return: the timestamp of the last update as saved in the DB or None (if update_timestamp_field was None)
        """
        record_to_update = self.get_record_by_id(record_id)
        list_to_update = record_to_update[list_label]
        list_to_update.append(item_value)
        if update_timestamp_label:
            last_update = time.time()
            record_to_update['last_update'] = last_update
        else:
            last_update = None
        res = self.client.index(index=self.database,doc_type=self.collection,body=record_to_update,id=record_id)
        self.logger.debug('updated %s document', res[u'_id'])
        return last_update

    def remove_from_list(self, record_id, list_label, item_value, update_timestamp_label=None):
        """
        Remove a value from a list within a document

        :param record_id: record's ID
        :param list_label: the label of the field containing the list
        :type list_label: string
        :param item_value: the item that will be removed from the list
        :param update_timestamp_label: the label of the *last_update* field of the record if the last update timestamp
          must be recorded or None
        :type update_timestamp_label: field label or None
        :return: the timestamp of the last update as saved in the DB or None (if update_timestamp_field was None)
        """
        record_to_update = self.get_record_by_id(record_id)
        list_to_update=record_to_update[list_label]
        list_to_update.remove(item_value)
        if update_timestamp_label:
            last_update = time.time()
            record_to_update['last_update'] = last_update
        else:
            last_update = None
        res = self.client.index(index=self.database,doc_type=self.collection,body=record_to_update,id=record_id)
        self.logger.debug('updated %s document', res[u'_id'])
        return last_update

    def parseExpression(self, expression):
        q = expression.replace('/','.')
        return q

    def parseSimpleExpression(self, expression):
        expr = {}
        operator = re.search('>|>=|=|<|<=|!=', expression)
        if operator:
            op1 = expression[0:operator.start()].strip('\'')
            op2 = expression[operator.end():].strip('\'')
            op = expression[operator.start():operator.end()]
            if re.match('=', op):
                expr[op1] = op2
            elif re.match('!=', op):
                expr[op1] = {'$ne' : op2}
            elif re.match('>', op):
                expr[op1] = {'$gt' : op2}
            elif re.match('>=', op):
                expr[op1] = {'$gte' : op2}
            elif re.match('<', op):
                expr[op1] = {'$lt' : op2}
            elif re.match('<=', op):
                expr[op1] = {'$lte' : op2}
            else:
                raise ParseSimpleExpressionException("Invalid operator")
        else:
            q = expression.replace('/','.')
            expr[q] = {'$exists' : True}
        return expr

    def parseMatchExpression(self, expr):
        range = expr.expression.lstrip('{')
        range = range.rstrip('}')
        values = range.split(',')
        final = []
        for val in values:
            v = val.strip('\'')
            final.append(v)
        return final

    def calculateConditionExpression(self, query, condition):
        i = 0
        or_expressions = []
        while i < len(condition.conditionSequence):
            expression = condition.conditionSequence[i]
            if isinstance(expression, ConditionExpression):
                print "Expression: " + expression.expression
                op1 = self.parseExpression(expression.expression)
                if not i+1==len(condition.conditionSequence):
                    operator = condition.conditionSequence[i+1]
                    if isinstance(operator, ConditionOperator):
                        if operator.op == "AND":
                            if condition.conditionSequence[i+2].beginswith('('):
                                op2 = self.mergeExpr(condition.conditionSequence[i+2:])
                            else:
                                op2 = self.mergeExpr(condition.conditionSequence[i+2:])
                            expr = {"$and" : {op1, op2}}
                            or_expressions.append(expr)
                            i = i+3
                        elif operator.op == "OR":
                            or_expressions.append(op1)
                            i = i+2
                        elif operator.op == "MATCHES":
                            match = self.parseMatchExpression(condition.conditionSequence[i+2])
                            expr = {op1 : {"$in" : match}}
                            or_expressions.append(expr)
                            i = i+3
                        elif operator.op == ">":
                            expr = {op1 : {"$gt" : {condition.conditionSequence[i+2].expression}}}
                            or_expressions.append(expr)
                            i = i+3
                        elif operator.op == "<":
                            expr = {op1 : {"$lt" : {condition.conditionSequence[i+2].expression}}}
                            or_expressions.append(expr)
                            i = i+3
                        elif operator.op == "=":
                            expr = {op1 : {"$eq" : {condition.conditionSequence[i+2].expression}}}
                            or_expressions.append(expr)
                            i = i+3
                        elif operator.op == ">=":
                            expr = {op1 : {"$gte" : {condition.conditionSequence[i+2].expression}}}
                            or_expressions.append(expr)
                            i = i+3
                        elif operator.op == "<=":
                            expr = {op1 : {"$lte" : {condition.conditionSequence[i+2].expression}}}
                            or_expressions.append(expr)
                            i = i+3
                        else:
                            pass
                        print "Operator: " + operator.op
                    else:
                        pass
                else:
                    or_expressions.append(self.parseSimpleExpression(op1))
                    i += 1
        if len(or_expressions) == 1:
            print "or_expression single: " + str(or_expressions[0])
            query.update(or_expressions[0])
        else:
            print "or_expression: " + str(or_expressions)
            query["$or"] = or_expressions

    def computePredicate(self, query, predicate):
        if isinstance(predicate, PredicateExpression):
            predEx = predicate.predicateExpression
            if predEx:
                lo = predEx.leftOperand
                if not lo:
                    raise PredicateException("MongoDriver.compute_predicate: No left operand found")
                op = predEx.operand
                ro = predEx.rightOperand
                if op and ro:
                    print "lo: %s - op: %s - ro: %s" % (lo, op, ro)
                    if op == "=":
                        query[lo] = ro
            else:
                raise PredicateException("MongoDriver.compute_predicate: No predicate expression found")
        elif isinstance(predicate, ArchetypePredicate):
            predicateString = predicate.archetypeId
            query[predicateString] = {'$exists' : True}
        else:
            raise PredicateException("MongoDriver.compute_predicate: No predicate expression found")

    def calculateLocationExpression(self, query, location):
        # Here is where the collection has been chosen according to the selection
        print "LOCATION: %s" % str(location)
        if location.classExpression:
            ce = location.classExpression
            className = ce.className
            variableName = ce.variableName
            predicate = ce.predicate
            if predicate:
                self.computePredicate(query, predicate)
        else:
            raise Exception("MongoDriver Exception: Query must have a location expression")

        for cont in location.containers:
            if cont.classExpr:
                ce = cont.classExpr
                className = ce.className
                variableName = ce.variableName
                predicate = ce.predicate
                if predicate:
                    self.computePredicate(query, predicate)
        print "QUERY: %s" % query
        print (self.collection)
        resp = self.collection.find(query)
        print resp.count()

    def createResponse(self, dbQuery, selection):
        # execute the query
        print "QUERY PRE: %s" % str(dbQuery)
        # Prepare the response
        rs = ResultSet()
        # Declaring a projection to retrieve only the selected fields
        proj = {}
        proj['_id'] = 0
        for var in selection.variables:
            columnDef = ResultColumnDef()
            columnDef.name = var.label
            columnDef.path = var.variable.path.value
            rs.columns.append(columnDef)
            projCol = columnDef.path.replace('/','.').strip('.')
            proj[projCol] = 1
        print "PROJ: %s" % str(proj)
        queryResult = self.collection.find(dbQuery, proj)
        rs.totalResults = queryResult.count()
        for q in queryResult:
            rr = ResultRow()
            rr = ResultRow()
            rr.items = q.values()
            rs.rows.append(rr)
        return rs

    def execute_query(self, query):
        self.__check_connection()
        try:
            selection = query.selection
            location = query.location
            condition = query.condition
            orderRules = query.orderRules
            timeConstraints = query.timeConstraints
            dbQuery = {}
            # select the collection
            self.calculateLocationExpression(dbQuery,location)
            # prepare the query to the db
            if condition:
                self.calculateConditionExpression(dbQuery,condition)
            # create the response
            return self.createResponse(dbQuery, selection)
        except Exception, e:
            print "Mongo Driver Error: " + str(e)
            return None