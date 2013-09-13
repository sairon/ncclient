# Copyright 2013 Jan Cermak
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

from Queue import Queue
import os
from threading import Thread, Lock, Event

from ncclient.xml_ import *
from ncclient.capabilities import Capabilities

from errors import TransportError

import logging
logger = logging.getLogger('ncclient.transport.session')

class Session(Thread):

    "Base class for use by transport protocol implementations."

    def __init__(self, capabilities):
        Thread.__init__(self)
        self.setDaemon(True)
        self._listeners = set()
        self._lock = Lock()
        self.setName('session')
        self._q = Queue()
        self._client_capabilities = capabilities
        self._server_capabilities = None # yet
        self._id = None # session-id
        self._connected = False # to be set/cleared by subclass implementation
        logger.debug('%r created: client_capabilities=%r' %
                     (self, self._client_capabilities))

    def _dispatch_message(self, raw):
        try:
            root = parse_root(raw)
        except Exception as e:
            logger.error('error parsing dispatch message: %s' % e)
            return
        with self._lock:
            listeners = list(self._listeners)
        for l in listeners:
            logger.debug('dispatching message to %r: %s' % (l, raw))
            l.callback(root, raw) # no try-except; fail loudly if you must!
    
    def _dispatch_error(self, err):
        with self._lock:
            listeners = list(self._listeners)
        for l in listeners:
            logger.debug('dispatching error to %r' % l)
            try: # here we can be more considerate with catching exceptions
                l.errback(err) 
            except Exception as e:
                logger.warning('error dispatching to %r: %r' % (l, e))

    def _post_connect(self):
        "Greeting stuff"
        init_event = Event()
        error = [None] # so that err_cb can bind error[0]. just how it is.
        # callbacks
        def ok_cb(id, capabilities):
            self._id = id
            self._server_capabilities = capabilities
            init_event.set()
        def err_cb(err):
            error[0] = err
            init_event.set()
        listener = HelloHandler(ok_cb, err_cb)
        self.add_listener(listener)
        self.send(HelloHandler.build(self._client_capabilities))
        logger.debug('starting main loop')
        self.start()
        # we expect server's hello message
        init_event.wait()
        # received hello message or an error happened
        self.remove_listener(listener)
        if error[0]:
            raise error[0]
        #if ':base:1.0' not in self.server_capabilities:
        #    raise MissingCapabilityError(':base:1.0')
        logger.info('initialized: session-id=%s | server_capabilities=%s' %
                    (self._id, self._server_capabilities))

    def add_listener(self, listener):
        """Register a listener that will be notified of incoming messages and
        errors.

        :type listener: :class:`SessionListener`
        """
        logger.debug('installing listener %r' % listener)
        if not isinstance(listener, SessionListener):
            raise SessionError("Listener must be a SessionListener type")
        with self._lock:
            self._listeners.add(listener)

    def remove_listener(self, listener):
        """Unregister some listener; ignore if the listener was never
        registered.

        :type listener: :class:`SessionListener`
        """
        logger.debug('discarding listener %r' % listener)
        with self._lock:
            self._listeners.discard(listener)

    def get_listener_instance(self, cls):
        """If a listener of the specified type is registered, returns the
        instance.

        :type cls: :class:`SessionListener`
        """
        with self._lock:
            for listener in self._listeners:
                if isinstance(listener, cls):
                    return listener

    def connect(self, *args, **kwds): # subclass implements
        raise NotImplementedError

    def run(self): # subclass implements
        raise NotImplementedError

    def send(self, message):
        """Send the supplied *message* (xml string) to NETCONF server."""
        if not self.connected:
            raise TransportError('Not connected to NETCONF server')
        logger.debug('queueing %s' % message)
        self._q.put(message)

    ### Properties

    @property
    def connected(self):
        "Connection status of the session."
        return self._connected

    @property
    def client_capabilities(self):
        "Client's :class:`Capabilities`"
        return self._client_capabilities

    @property
    def server_capabilities(self):
        "Server's :class:`Capabilities`"
        return self._server_capabilities

    @property
    def id(self):
        """A string representing the `session-id`. If the session has not been initialized it will be `None`"""
        return self._id


class Rfc4742Session(Session):
    """
    Base class providing parsing methods for :rfc:`4742` NETCONF session over SSH.
    """

    # v1.0: RFC 4742
    MSG_DELIM = "]]>]]>"
    # v1.1: RFC 6242
    END_DELIM = '\n##\n'

    def __init__(self, capabilities):
        super(Rfc4742Session, self).__init__(capabilities)
        self._buffer = StringIO()  # for incoming data
        # parsing-related, see _parse()
        self._parsing_state10 = 0
        self._parsing_pos10 = 0
        self._parsing_pos11 = 0
        self._parsing_state11 = 0
        self._expchunksize = 0
        self._curchunksize = 0
        self._inendpos = 0
        self._message = []

    def _parse10(self):

        """Messages are delimited by MSG_DELIM. The buffer could have grown by
        a maximum of BUF_SIZE bytes everytime this method is called. Retains
        state across method calls and if a byte has been read it will not be
        considered again."""

        logger.debug("parsing netconf v1.0")
        delim = self.MSG_DELIM
        n = len(delim) - 1
        expect = self._parsing_state10
        buf = self._buffer
        buf.seek(self._parsing_pos10)
        while True:
            x = buf.read(1)
            if not x:  # done reading
                break
            elif x == delim[expect]:  # what we expected
                expect += 1  # expect the next delim char
            else:
                expect = 0
                continue
                # loop till last delim char expected, break if other char encountered
            for i in range(expect, n):
                x = buf.read(1)
                if not x:  # done reading
                    break
                if x == delim[expect]:  # what we expected
                    expect += 1  # expect the next delim char
                else:
                    expect = 0  # reset
                    break
            else:  # if we didn't break out of the loop, full delim was parsed
                msg_till = buf.tell() - n
                buf.seek(0)
                logger.debug('parsed new message')
                self._dispatch_message(buf.read(msg_till).strip())
                buf.seek(n + 1, os.SEEK_CUR)
                rest = buf.read()
                buf = StringIO()
                buf.write(rest)
                buf.seek(0)
                expect = 0
        self._buffer = buf
        self._parsing_state = expect
        self._parsing_pos10 = self._buffer.tell()

    def _parse11(self):
        logger.debug("parsing netconf v1.1")
        message = self._message
        expchunksize = self._expchunksize
        curchunksize = self._curchunksize
        idle, instart, inmsg, inbetween, inend = range(5)
        state = self._parsing_state11
        inendpos = self._inendpos
        MAX_STARTCHUNK_SIZE = 10  # 4294967295
        pre = 'invalid base:1:1 frame'
        buf = self._buffer
        buf.seek(self._parsing_pos11)
        num = []

        while True:
            x = buf.read(1)
            if not x:
                break  # done reading
            # logger.debug('x: %s', x)
            if state == idle:
                if x == '\n':
                    state = instart
                    inendpos = 1
                else:
                    logger.debug("buf:%s" % buf.getvalue())
                    logger.debug('%s (%s: expect newline)' % (pre, state))
                    raise Exception
            elif state == instart:
                if inendpos == 1:
                    if x == '#':
                        inendpos += 1
                    else:
                        logger.debug('%s (%s: expect "#")' % (pre, state))
                        raise Exception
                elif inendpos == 2:
                    if x.isdigit():
                        inendpos += 1  # == 3 now #
                        num.append(x)
                    else:
                        logger.debug('%s (%s: expect digit)' % (pre, state))
                        raise Exception
                else:
                    if inendpos == MAX_STARTCHUNK_SIZE:
                        logger.debug('%s (%s: no. too long)' % (pre, state))
                        raise Exception
                    elif x == '\n':
                        num = ''.join(num)
                        try:
                            num = long(num)
                        except:
                            logger.debug('%s (%s: invalid no.)' % (pre, state))
                            raise Exception
                        else:
                            state = inmsg
                            expchunksize = num
                            logger.debug('response length: %d' % expchunksize)
                            curchunksize = 0
                            inendpos += 1
                    elif x.isdigit():
                        inendpos += 1  # > 3 now #
                        num.append(x)
                    else:
                        logger.debug('%s (%s: expect digit)' % (pre, state))
                        raise Exception
            elif state == inmsg:
                message.append(x)
                curchunksize += 1
                chunkleft = expchunksize - curchunksize
                if chunkleft == 0:
                    inendpos = 0
                    state = inbetween
                    message = ''.join(message)
                    logger.debug('parsed new message: %s' % message)
            elif state == inbetween:
                if inendpos == 0:
                    if x == '\n':
                        inendpos += 1
                    else:
                        logger.debug('%s (%s: expect newline)' % (pre, state))
                        raise Exception
                elif inendpos == 1:
                    if x == '#':
                        inendpos += 1
                    else:
                        logger.debug('%s (%s: expect "#")' % (pre, state))
                        raise Exception
                else:
                    if x == '#':
                        inendpos += 1  # == 3 now #
                        state = inend
                    else:
                        logger.debug('%s (%s: expect "#")' % (pre, state))
                        raise Exception
            elif state == inend:
                if inendpos == 3:
                    if x == '\n':
                        inendpos = 0
                        state = idle
                        logger.debug('dispatching message')
                        self._dispatch_message(message)
                        # reset
                        rest = buf.read()
                        buf = StringIO()
                        buf.write(rest)
                        buf.seek(0)
                        message = []
                        expchunksize = chunksize = 0
                        parsing_state11 = idle
                        inendpos = parsing_pos11 = 0
                        break
                    else:
                        logger.debug('%s (%s: expect newline)' % (pre, state))
                        raise Exception
            else:
                logger.debug('%s (%s invalid state)' % (pre, state))
                raise Exception

        self._message = message
        self._expchunksize = expchunksize
        self._curchunksize = curchunksize
        self._parsing_state11 = state
        self._inendpos = inendpos
        self._buffer = buf
        self._parsing_pos11 = self._buffer.tell()
        logger.debug('parse11 ending ...')


class SessionListener(object):

    """Base class for :class:`Session` listeners, which are notified when a new
    NETCONF message is received or an error occurs.

    .. note::
        Avoid time-intensive tasks in a callback's context.
    """

    def callback(self, root, raw):
        """Called when a new XML document is received. The *root* argument allows the callback to determine whether it wants to further process the document.

        Here, *root* is a tuple of *(tag, attributes)* where *tag* is the qualified name of the root element and *attributes* is a dictionary of its attributes (also qualified names).

        *raw* will contain the XML document as a string.
        """
        raise NotImplementedError

    def errback(self, ex):
        """Called when an error occurs.

        :type ex: :exc:`Exception`
        """
        raise NotImplementedError


class HelloHandler(SessionListener):

    def __init__(self, init_cb, error_cb):
        self._init_cb = init_cb
        self._error_cb = error_cb

    def callback(self, root, raw):
        tag, attrs = root
        if (tag == qualify("hello")) or (tag == "hello"):
            try:
                id, capabilities = HelloHandler.parse(raw)
            except Exception as e:
                self._error_cb(e)
            else:
                self._init_cb(id, capabilities)

    def errback(self, err):
        self._error_cb(err)

    @staticmethod
    def build(capabilities):
        "Given a list of capability URI's returns <hello> message XML string"
        hello = new_ele("hello")
        caps = sub_ele(hello, "capabilities")
        def fun(uri): sub_ele(caps, "capability").text = uri
        map(fun, capabilities)
        return to_xml(hello)

    @staticmethod
    def parse(raw):
        "Returns tuple of (session-id (str), capabilities (Capabilities)"
        sid, capabilities = 0, []
        root = to_ele(raw)
        for child in root.getchildren():
            if child.tag == qualify("session-id") or child.tag == "session-id":
                sid = child.text
            elif child.tag == qualify("capabilities") or child.tag == "capabilities" :
                for cap in child.getchildren():
                    if cap.tag == qualify("capability") or cap.tag == "capability":
                        capabilities.append(cap.text)
        return sid, Capabilities(capabilities)
