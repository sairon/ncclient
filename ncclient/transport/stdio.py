# Copyright 2013 Jan Cermak
# Copyright 2012 Vaibhav Bajpai
# Copyright 2009 Shikhar Bhushan
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import fcntl
from select import select
from subprocess import Popen, PIPE

from session import Rfc4742Session
from ncclient.xml_ import *

import logging

logger = logging.getLogger("ncclient.transport.stdio")

TICK = 0.1


class StdIOSession(Rfc4742Session):
    def __init__(self, capabilities):
        super(StdIOSession, self).__init__(capabilities)
        self._process = None
        self._connected = False

    def close(self):
        if self._process.poll() is None:
            self._process.terminate()
        self._connected = False

    def connect(self, path):
        """
        Create a subprocess with a NETCONF server that communicates with ncclient
        using piped stdio.

        Arguments:
            *path* - path to the server binary
        """
        self._process = Popen(path, shell=False, bufsize=1,
                              stdin=PIPE, stdout=PIPE, stderr=open(os.devnull, "w"),
                              close_fds=True)
        fcntl.fcntl(self._process.stdout.fileno(), fcntl.F_SETFL, os.O_NONBLOCK)
        self._connected = True

        self._post_connect()

    def run(self):
        queue = self._q

        start_delim = lambda data_len: '\n#%s\n' % data_len

        try:
            while True:
                r, w, x = select([self._process.stdout], [], [], 0.1)

                if r:
                    # reading data
                    data = r[0].read()
                    if data:
                        self._buffer.write(data)
                        if self._server_capabilities:
                            if 'urn:ietf:params:netconf:base:1.1' in self._server_capabilities and 'urn:ietf:params:netconf:base:1.1' in self._client_capabilities:
                                self._parse11()
                            elif 'urn:ietf:params:netconf:base:1.0' in self._server_capabilities or 'urn:ietf:params:netconf:base:1.0' in self._client_capabilities:
                                self._parse10()
                            else:
                                raise Exception("No capabilities for reading data.")
                        else:
                            self._parse10()  # HELLO msg uses EOM markers.
                if not queue.empty():
                    # writing data
                    data = queue.get()
                    try:
                        # send a HELLO msg using v1.0 EOM markers.
                        validated_element(data, tags='{urn:ietf:params:xml:ns:netconf:base:1.0}hello')
                        data = "%s%s" % (data, self.MSG_DELIM)
                    except XMLError:
                        # this is not a HELLO msg
                        # we publish v1.1 support
                        if 'urn:ietf:params:netconf:base:1.1' in self._client_capabilities:
                            if self._server_capabilities:
                                if 'urn:ietf:params:netconf:base:1.1' in self._server_capabilities:
                                    # send using v1.1 chunked framing
                                    data = "%s%s%s" % (start_delim(len(data)), data, self.END_DELIM)
                                elif 'urn:ietf:params:netconf:base:1.0' in self._server_capabilities:
                                    # send using v1.0 EOM markers
                                    data = "%s%s" % (data, self.MSG_DELIM)
                                else:
                                    raise Exception("No capabilities for writing data.")
                            else:
                                logger.error('HELLO msg was sent, but server capabilities are still not known')
                                raise
                        # we publish only v1.0 support
                        else:
                            # send using v1.0 EOM markers
                            data = "%s%s" % (data, self.MSG_DELIM)
                    finally:
                        logger.debug("Sending: %s", data)
                        self._process.stdin.write(data)
        except Exception as e:
            logger.error("Broke out of main loop, error=%r", e)
            self.close()
            self._dispatch_error(e)

    @property
    def process(self):
        return self._process
