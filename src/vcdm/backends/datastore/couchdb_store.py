##
# Copyright 2002-2012 Ilja Livenson, PDC KTH
# 
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
##
import socket
import time
from uuid import uuid4
import sys
from distutils.version import StrictVersion

import couchdb

from vcdm.config import get_config
from vcdm.errors import InternalError


config = get_config()


class CouchDBStore(object):

    db = None

    def __init__(self):
        server = couchdb.Server(config.get('couchdb', 'datastore.endpoint'))
        # assure version is supported
        try:
            version = server.version()
            assert StrictVersion(version) > '1.0'
        except socket.error as e:
            print "Failed to connect to a CouchDB instance at %s" % config.get('couchdb', 'datastore.endpoint')
            print "[%s] %s" % (e.errno, e.strerror)
            sys.exit(-1)
        except AssertionError:
            print "Couchdb server version '%s' is not supported. At least version 1.0 is required." % version
            sys.exit(-1)

        if 'meta' not in server:
            self.db = server.create('meta')
        else:
            # already created
            self.db = server['meta']
        # make sure we have a top-level folder
        if self.find_by_path('/', 'container')[0] is None:
            self.write({
                        'object': 'container',
                        'fullpath': '/',
                        'name': '/',
                        'parent_container': '/',
                        'children': {},
                        'metadata': {},
                        'owner': 'system',
                        'ctime': str(time.time()),
                        'mtime': str(time.time())}, None)

    def read(self, docid):
        return self.db[docid]

    def write(self, data, docid=None):
        if docid is None:
            docid = uuid4().hex
        if docid in self.db:
            doc = self.db[docid]
            doc.update(data)
            self.db.save(doc)
        else:
            data['_id'] = docid
            self.db.save(data)
        return docid

    def exists(self, docid):
        return (docid in self.db)

    def delete(self, docid):
        del self.db[docid]

    def find_uid_match(self, pattern):
        """ Return UIDs that correspond to a objects with a path matching the pattern """

        dirn_fun = '''
        function(doc) {
           if (doc.fullpath.match(/^%s/)) {
               emit(doc.id, doc.fullpath);
           }
        }
        ''' % pattern.replace("/", "\\/")

        return list(self.db.query(dirn_fun))

    def get_total_blob_size(self, start_time, end_time, avatar='Anonymous'):
        """ Return total size in GBs of all blobs indexed by the datastore. """

        dirn_fun = '''
        function(doc) {
           if (doc.ctime > %s && doc.ctime < %s && doc.object == 'blob' && doc.owner == '%s') {
               emit(doc.size, null);
           }
        }
        ''' % (start_time, end_time, avatar)

        return sum([x.key for x in self.db.query(dirn_fun)])

    def get_all_avatars(self):
        """Return all avatars that have an entry in the system"""
        dirn_fun = '''
        function(doc) {
           if (doc.object == 'blob' || doc.object == 'container') {
               emit(doc.owner, 1);
           }
        }
        '''
        reducer = '''
        function(keys, values) {
            var a = [], l = keys.length;
            for(var i=0; i<l; i++) {
                for(var j=i+1; j<l; j++)
                    if (keys[i][0] === keys[j][0]) j = ++i;
                    a.push(keys[i][0]);
            }
            return a;
        }
        '''

        res = list(self.db.query(dirn_fun, reduce_fun=reducer, options='group=true'))
        return res[0].value if res[0].value is not None else []

    def find_by_property(self, property_name, property_value, object_type=None, fields=None):
        """ Find an object by a given property.
        - object_type - optional filter by the type of an object (e.g. blob, container, ...)
        - fields - fields to retrieve from the database. By default only gets UID of an object
        """
        comparision_string = 'true'
        if object_type is not None:
            comparision_string = "doc.object == '%s'" % object_type

        if fields is not None:
            fields = '{' + ','.join([f + ': doc.' + f for f in fields]) + '}'
        else:
            fields = 'null'

        fnm_fun = '''function(doc) {
            if (doc.%s == '%s' && %s ) {
                emit(doc.id, %s);
            }
        }
        ''' % (property_name, property_value, comparision_string, fields)
        res = self.db.query(fnm_fun)
        if len(res) == 0:
            return (None, {})
        elif len(res) > 1:
            # XXX: does CDMI allow this in case of references/...?
            raise InternalError("Namespace collision: more than one UID corresponds to an object.")
        else:
            tmp_res = list(res)[0]
            return (tmp_res.id, tmp_res.value)

    def find_by_path(self, path, object_type=None, fields=None):
        """ Find an object at a given path.
        - object_type - optional filter by the type of an object (e.g. blob, container, ...)
        - fields - fields to retrieve from the database. By default only gets UID of an object
        """
        return self.find_by_property('fullpath', path, object_type, fields)

    def find_by_uid(self, uid, object_type=None, fields=None):
        """ Find an object with a given UID.
        - object_type - optional filter by the type of an object (e.g. blob, container, ...)
        - fields - fields to retrieve from the database. By default only gets UID of an object
        """
        return self.find_by_property('_id', uid, object_type, fields)

    def find_path_uids(self, paths):
        """Return a list of IDs of container objects that correspond to the specified path."""
        comparision_string = ['doc.fullpath == "' + p + '"' for p in paths]
        fnm_fun = '''function(doc) {
            if (doc.object == 'container' && (%s)) {
                emit(doc.id, null);
            }
        }
        ''' % ' || '.join(comparision_string)
        res = self.db.query(fnm_fun)
        if len(res) == 0:
            return None
        else:
            return list(res)
