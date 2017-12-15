#!/usr/bin/env python3

import sys, socket, _thread, re, time, logging, uuid
import xml.etree.ElementTree as ET


class XMPPServer():
    server_id = 'bumper'
    bot_id = 'bumpy'
    client_id = None
    clients = []

    def __init__(self):
        try:
            # Initialize bot server
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_address = (socket.gethostbyname(socket.gethostname()), 5223)
            server.bind(server_address)
            server.listen(1)
            logging.info('XMPPServer: listening on {}:{}'.format(server_address[0], server_address[1]))
            while True:
                logging.info('XMPPServer: awaiting connection')
                connection, client_address = server.accept()
                # disconnect any clients with this ip
                for client in self.clients:
                    if client.address == client_address[0]:
                        client.disconnect()
                _thread.start_new_thread(Client,(connection, client_address))
        except KeyboardInterrupt:
            logging.info('keyboard interrupt')
            server.shutdown(2)
            server.close()
        except Exception as e:
            server.shutdown(2)
            server.close()
            logging.error(e)


class Client():
    IDLE = 0
    CONNECT = 1
    INIT = 2
    BIND = 3
    READY = 4
    DISCONNECT = 5
    UNKNOWN = 0
    BOT = 1
    CONTROLLER = 2

    def __init__(self, connection, client_address):
        self.id = uuid.uuid4()
        self.type = self.UNKNOWN
        self.state = self.IDLE
        self.connection = connection
        self.address = client_address[0]
        XMPPServer.clients.append(self)
        self._main()

    def send(self, command):
        logging.debug('to {}: {}'.format(self.address, command))
        self.connection.send(command.encode())

    def disconnect(self):
        logging.info('{} disconnecting'.format(self.address))
        self.connection.close()
        self._set_state('DISCONNECT')

    def _tag_strip_uri(self, tag):
        if tag[0] == "{":
            uri, ignore, tag = tag[1:].partition("}")
        return tag

    def _set_state(self, state):
        new_state = getattr(Client, state)
        if self.state > new_state:
            raise Exception("{} illegal state change {}->{}".format(self.address, self.state, new_state))
        logging.info('{} state: {}'.format(self.address, state))
        self.state = new_state

    def _handle_ctl(self, xml, data):
        ctl = xml[0][0]
        if ctl.get('admin') and self.type == self.BOT:
            logging.info('admin username received from bot: {}'.format(ctl.get('admin')))
            XMPPServer.client_id = ctl.get('admin')
            return
        # forward
        for client in XMPPServer.clients:
            if client.address != self.address and client.state == client.READY:
                if client.type == self.BOT:
                    data = data.decode('utf-8')
                    id_index = data.find('id')
                    if id_index > -1:
                        data = data[:id_index] + 'from="' + XMPPServer.client_id + '" ' + data[id_index:]
                        data = data.encode()
                client.send(data.decode('utf-8'))

    def _handle_result(self, data):
        # forward
        for client in XMPPServer.clients:
            if client.address != self.address and client.state == client.READY:
                logging.debug('sending result: ' + data.decode('utf-8'))
                client.send(data.decode('utf-8'))

    def _main(self):
        try:
            logging.info('client connected: {}'.format(self.address))
            self._set_state('CONNECT')
            while True:
                data = self.connection.recv(4096)
                if data:
                    logging.debug('from {}: {}'.format(self.address, data.decode('utf-8')))
                    try:
                        if self.state == self.CONNECT:
                            if data.decode('utf-8').find('jabber:client') > -1:
                                self._set_state('INIT')
                                # ack jabbr:client
                                self.send('<stream:stream xmlns:stream="http://etherx.jabber.org/streams" xmlns="jabber:client" version="1.0" id="1" from="{}">'.format(XMPPServer.server_id))
                                time.sleep(0.5)
                                # session
                                self.send('<stream:features><bind xmlns="urn:ietf:params:xml:ns:xmpp-bind"/><session xmlns="urn:ietf:params:xml:ns:xmpp-session"/></stream:features>')
                            continue
                        xml = ET.fromstring(data)
                        if len(xml):
                            child = self._tag_strip_uri(xml[0].tag)
                        else:
                            child = None
                        if xml.get('id'):
                            last_id = xml.get('id')
                        else:
                            last_id = '0'
                        if xml.tag == 'iq':
                            res = None
                            if child == 'bind':
                                res = '<iq type="result" id="{}"><bind xmlns="urn:ietf:params:xml:ns:xmpp-bind"><jid>{}</jid></bind></iq>'.format(last_id, XMPPServer.bot_id)
                                self._set_state('BIND')
                            elif child == 'session':
                                res = '<iq type="result" id="{}" />'.format(last_id)
                                self._set_state('READY')
                            elif child == 'query':
                                self._handle_ctl(xml, data)
                            elif child == 'ping':
                                # respond to ping request
                                res = '<iq type="result" id="{}" from="{}" />'.format(last_id, xml.get('to'))
                            elif xml.get('type') == 'result':
                                self._handle_result(data)
                            if res:
                                self.send(res)
                        elif xml.tag == 'presence':
                            if len(xml) and xml[0].tag == 'status':
                                # bot announcing arrival
                                self.type = self.BOT
                                logging.info('{} type set to BOT (based on presence tag)'.format(self.address))
                                # send a command from an unknown user  - the response will contain the correct admin username
                                self.send('<iq type="set" id="{}" from="{}" to="{}"><query xmlns="com:ctl"><ctl td="GetCleanState" /></query></iq>'.format(uuid.uuid4(), 'unknown@ecouser.net', XMPPServer.bot_id))
                            elif xml.get('type') == 'available':
                                self.type = self.CONTROLLER
                                logging.info('{} type set to CONTROLLER (based on presence tag)'.format(self.address))
                    except ET.ParseError as e:
                        logging.debug("error: {}".format(e))
                    except Exception as e:
                        logging.error(e)
                        self._set_state('DISCONNECT')
        except Exception as e:
            logging.error(e)
            self._set_state('DISCONNECT')
        finally:
            self.disconnect()
