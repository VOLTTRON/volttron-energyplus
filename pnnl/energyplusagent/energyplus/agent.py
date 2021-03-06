# -*- coding: utf-8 -*- {{{
# vim: set fenc=utf-8 ft=python sw=4 ts=4 sts=4 et:
#
# Copyright (c) 2016, Battelle Memorial Institute
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice, this
#    list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
# ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
# The views and conclusions contained in the software and documentation are those
# of the authors and should not be interpreted as representing official policies,
# either expressed or implied, of the FreeBSD Project.
#

# This material was prepared as an account of work sponsored by an
# agency of the United States Government.  Neither the United States
# Government nor the United States Department of Energy, nor Battelle,
# nor any of their employees, nor any jurisdiction or organization
# that has cooperated in the development of these materials, makes
# any warranty, express or implied, or assumes any legal liability
# or responsibility for the accuracy, completeness, or usefulness or
# any information, apparatus, product, software, or process disclosed,
# or represents that its use would not infringe privately owned rights.
#
# Reference herein to any specific commercial product, process, or
# service by trade name, trademark, manufacturer, or otherwise does
# not necessarily constitute or imply its endorsement, recommendation,
# r favoring by the United States Government or any agency thereof,
# or Battelle Memorial Institute. The views and opinions of authors
# expressed herein do not necessarily state or reflect those of the
# United States Government or any agency thereof.
#
# PACIFIC NORTHWEST NATIONAL LABORATORY
# operated by BATTELLE for the UNITED STATES DEPARTMENT OF ENERGY
# under Contract DE-AC05-76RL01830

#}}}

from __future__ import absolute_import


import logging
import os
import socket
import subprocess
import sys
from datetime import datetime
from gevent import monkey
monkey.patch_socket()
from volttron.platform.agent import utils
from volttron.platform.vip.agent import Core, RPC
from pnnl.pubsubagent.pubsub.agent import SynchronizingPubSubAgent


utils.setup_logging()
log = logging.getLogger(__name__)
SUCCESS = 'SUCCESS'
FAILURE = 'FAILURE'

    
class EnergyPlusAgent(SynchronizingPubSubAgent):


    def __init__(self, config_path, **kwargs):
        super(EnergyPlusAgent, self).__init__(config_path, **kwargs)
        self.version = 8.4
        self.bcvtb_home = '.'
        self.model = None
        self.weather = None
        self.socketFile = None
        self.variableFile = None
        self.time = 0
        self.vers = 2
        self.flag = 0
        self.sent = None
        self.rcvd = None
        self.socketServer = None
        self.simulation = None
        self.step = None
        self.ePlusInputs = 0
        self.ePlusOutputs = 0
        if not self.config:
            self.exit('No configuration found.')
        self.cwd = os.getcwd()


    @Core.receiver('onsetup')
    def setup(self, sender, **kwargs):
        super(EnergyPlusAgent, self).setup(sender, **kwargs)  
        
        
    @Core.receiver('onstart')
    def start(self, sender, **kwargs):
        self.subscribe()
        self.startSocketServer()
        self.startSimulation()


    def startSocketServer(self):
        self.socketServer = self.SocketServer()
        self.socketServer.onRecv = self.recvEnergyPlusMssg
        self.socketServer.connect()
        self.core.spawn(self.socketServer.start)    
        
        
    def startSimulation(self):
        if not self.model:
            self.exit('No model specified.')
        if not self.weather:
            self.exit('No weather specified.')
        modelPath = self.model
        if (modelPath[0] == '~'):
            modelPath = os.path.expanduser(modelPath)
        if (modelPath[0] != '/'):
            modelPath = os.path.join(self.cwd,modelPath)
        weatherPath = self.weather
        if (weatherPath[0] == '~'):
            weatherPath = os.path.expanduser(weatherPath)
        if (weatherPath[0] != '/'):
            weatherPath = os.path.join(self.cwd,weatherPath)
        modelDir = os.path.dirname(modelPath)
        bcvtbDir = self.bcvtb_home
        if (bcvtbDir[0] == '~'):
            bcvtbDir = os.path.expanduser(bcvtbDir)
        if (bcvtbDir[0] != '/'):
            bcvtbDir = os.path.join(self.cwd,bcvtbDir)
        log.debug('Working in %r', modelDir)
        self.writePortFile(os.path.join(modelDir,'socket.cfg'))
        self.writeVariableFile(os.path.join(modelDir,'variables.cfg'))
        if (self.version >= 8.4):
            cmdStr = "cd %s; export BCVTB_HOME=\"%s\"; energyplus -w \"%s\" -r \"%s\"" % (modelDir, bcvtbDir, weatherPath, modelPath)
        else:
            cmdStr = "export BCVTB_HOME=\"%s\"; runenergyplus \"%s\" \"%s\"" % (bcvtbDir, modelPath, weatherPath)
        log.debug('Running: %s', cmdStr)
        self.simulation = subprocess.Popen(cmdStr, shell=True)
    
    
    def sendEnergyPlusMssg(self):
        if self.socketServer:
            args = self.input()
            mssg = '%r %r %r 0 0 %r' % (self.vers, self.flag, self.ePlusInputs, self.time)
            for obj in args.itervalues():
                if obj.get('name', None) and obj.get('type', None):
                    mssg = mssg + ' ' + str(obj.get('value'))
            self.sent = mssg+'\n'
            log.info('Sending message to EnergyPlus: ' + mssg)
            self.socketServer.send(self.sent)


    def recvEnergyPlusMssg(self, mssg):
        self.rcvd = mssg
        self.parseEnergyPlusMssg(mssg)
        self.publishAllOutputs()


    def parseEnergyPlusMssg(self, mssg):
        mssg = mssg.rstrip()
        log.info('Received message from EnergyPlus: ' + mssg)
        arry = mssg.split()
        slot = 6
        flag = arry[1]
        output = self.output()
        if flag != '0':
            if flag == '1':
                self.exit('Simulation reached end: ' + flag)
            elif flag == '-1':
                self.exit('Simulation stopped with unspecified error: ' + flag)
            elif flag == '-10':
                self.exit('Simulation stopped with error during initialization: ' + flag)
            elif flag == '-20':
                self.exit('Simulation stopped with error during time integration: ' + flag)
            else:
                self.exit('Simulation stopped with error code ' + flag)
        elif ((arry[2] < self.ePlusOutputs) and (len(arry) < self.ePlusOutputs+6)):
            self.exit('Got message with ' + arry[2] + ' inputs. Expecting ' + str(self.ePlusOutputs) + '.')
        else:
            if float(arry[5]): 
                self.time = float(arry[5])
            for key in output:
                if self.output(key, 'name') and self.output(key, 'type'):
                    try:
                        self.output(key, 'value', float(arry[slot]))
                    except:
                        self.exit('Unable to convert received value to double.')
                    slot += 1


    def exit(self, mssg):
        self.stop()
        log.error(mssg)
             

    def stop(self):
        if self.socketServer:
            self.socketServer.stop()
            self.socketServer = None
            

    def writePortFile(self, path):
        fh = open(path, "w+")
        fh.write('<?xml version="1.0" encoding="ISO-8859-1"?>\n')
        fh.write('<BCVTB-client>\n')
        fh.write('  <ipc>\n')
        fh.write('    <socket port="%r" hostname="%s"/>\n' % (self.socketServer.port, self.socketServer.host))
        fh.write('  </ipc>\n')
        fh.write('</BCVTB-client>')
        fh.close()


    def writeVariableFile(self, path):
        fh = open(path, "w+")
        fh.write('<?xml version="1.0" encoding="ISO-8859-1"?>\n')
        fh.write('<!DOCTYPE BCVTB-variables SYSTEM "variables.dtd">\n')
        fh.write('<BCVTB-variables>\n')
        for obj in self.output().itervalues():
            if obj.has_key('name') and obj.has_key('type'):
                self.ePlusOutputs = self.ePlusOutputs + 1
                fh.write('  <variable source="EnergyPlus">\n')
                fh.write('    <EnergyPlus name="%s" type="%s"/>\n' % (obj.get('name'), obj.get('type')))
                fh.write('  </variable>\n')
        for obj in self.input().itervalues():
            if obj.has_key('name') and obj.has_key('type'):
                self.ePlusInputs = self.ePlusInputs + 1
                fh.write('  <variable source="Ptolemy">\n')
                fh.write('    <EnergyPlus %s="%s"/>\n' % (obj.get('type'), obj.get('name')))
                fh.write('  </variable>\n')
        fh.write('</BCVTB-variables>\n')
        fh.close()
        
        
    @RPC.export    
    def request_new_schedule(self, requester_id, task_id, priority, requests):
        """RPC method
        
        Requests one or more blocks on time on one or more device.
        In this agent, this does nothing!
        
        :param requester_id: Requester name. 
        :param task_id: Task name.
        :param priority: Priority of the task. Must be either HIGH, LOW, or LOW_PREEMPT
        :param requests: A list of time slot requests
        
        :type requester_id: str
        :type task_id: str
        :type priority: str
        :type request: list
        :returns: Request result
        :rtype: dict
        
        """
        log.debug(requester_id + " requests new schedule " + task_id + " " + str(requests))
        result = {'result':SUCCESS, 
                   'data': {}, 
                   'info':''}
        return result
    
    
    @RPC.export 
    def request_cancel_schedule(self, requester_id, task_id):
        """RPC method
        
        Requests the cancelation of the specified task id.
        In this agent, this does nothing!
        
        :param requester_id: Requester name. 
        :param task_id: Task name.
        
        :type requester_id: str
        :type task_id: str
        :returns: Request result
        :rtype: dict
        
        """
        log.debug(requester_id + " canceled " + task_id)
        result = {'result':SUCCESS,
                   'data': {},
                   'info': ''}
        return result   
        
        
    @RPC.export
    def get_point(self, topic, **kwargs):
        """RPC method
         
        Gets the value of a specific point on a device_name. 
        Does not require the device_name be scheduled. 
         
        :param topic: The topic of the point to grab in the 
                      format <device_name topic>/<point name>
        :param **kwargs: These get dropped on the floor
        :type topic: str
        :returns: point value
        :rtype: any base python type
         
        """
        obj = self.getBestMatch(topic)
        if obj is not None: # we have an exact match to the  <device_name topic>/<point name>, so return the first value
            return obj.get('value', None)
        return None

            
    @RPC.export
    def set_point(self, requester_id, topic, value, **kwargs):
        """RPC method
        
        Sets the value of a specific point on a device. 
        Does not require the device be scheduled. 
        
        :param requester_id: Identifier given when requesting schedule. 
        :param topic: The topic of the point to set in the 
                      format <device topic>/<point name>
        :param value: Value to set point to.
        :param **kwargs: These get dropped on the floor
        :type topic: str
        :type requester_id: str
        :type value: any basic python type
        :returns: value point was actually set to.
        :rtype: any base python type
        
        """
        topic = topic.strip('/')
        log.debug("Attempting to write "+topic+" with value: "+str(value))
        result = self.updateTopicRpc(requester_id, topic, value)
        log.debug("Writing: {topic} : {value} {result}".format(topic=topic,
                                          value=value,
                                          result=result))
        if result==SUCCESS:
            return value;
        else:
            raise RuntimeError("Failed to set value: " + result)
     
     
    @RPC.export
    def revert_point(self, requester_id, topic, **kwargs):
        """RPC method
         
        Reverts the value of a specific point on a device to a default state. 
        Does not require the device be scheduled. 
         
        :param requester_id: Identifier given when requesting schedule. 
        :param topic: The topic of the point to revert in the 
                      format <device topic>/<point name>
        :param **kwargs: These get dropped on the floor
        :type topic: str
        :type requester_id: str
         
        """
        obj = self.getGetBestMatch(topic)
        if obj and obj.has_key('default'):
            value = obj.get('default')
            log.debug("Reverting topic "+topic+" to "+str(value))
            self.updateTopicRpc(requester_id, topic, value)
        else:
            log.warning("Unable to revert topic. No topic match or default defined!")


    @RPC.export
    def revert_device(self, requester_id, device_name, **kwargs): 
        """RPC method
         
        Reverts all points on a device to a default state. 
        Does not require the device be scheduled. 
         
        :param requester_id: Identifier given when requesting schedule. 
        :param topic: The topic of the device to revert (without a point!)
        :param **kwargs: These get dropped on the floor
        :type topic: str
        :type requester_id: str
         
        """
        device_name = device_name.strip('/')
        objs = self.getInputsFromTopic(device_name) # we will assume that the topic is only the <device topic> and revert all matches at this level!
        if objs is not None:
            for obj in objs:
                point_name = obj.get('field', None)
                topic = device_name+"/"+point_name if point_name else device_name
                if obj.has_key('default'):
                    value = obj.get('default')
                    log.debug("Reverting "+topic+" to "+str(value))
                    self.updateTopicRpc(requester_id, topic, value)
                else:
                    log.warning("Unable to revert "+topic+". No default defined!")
        
    
    def updateTopicRpc(self, requester_id, topic, value):
        obj = self.findBestMatch(topic)
        if obj is not None:
            obj['value'] = value
            obj['last_update'] = datetime.utcnow().isoformat(' ') + 'Z'
            self.onUpdateTopicRpc(requester_id, topic, value)
            return SUCCESS
        return FAILURE
             
             
    def onUpdateTopicRpc(self, requester_id, topic, value):
        self.updateComplete()
        
        
    def onUpdateComplete(self):
        self.sendEnergyPlusMssg()
        
        
    class SocketServer():


        def __init__(self, **kwargs):
            self.sock = None
            self.size = 4096
            self.client = None
            self.sent = None
            self.rcvd = None
            self.host = None
            self.port = None
        
        
        def onRecv(self, mssg):
            log.debug('Received %s' % mssg)
            
            
        def run(self):
            self.listen()
        
        
        def connect(self):
            if self.host is None:
                self.host = socket.gethostname()
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if self.port is None:
                self.sock.bind((self.host, 0))
                self.port = self.sock.getsockname()[1]
            else:
                self.sock.bind((self.host, self.port))
            log.debug('Bound to %r on %r' % (self.port, self.host))
            
    
        def send(self, mssg):
            self.sent = mssg
            if self.client is not None and self.sock is not None:
                try:
                    self.client.send(self.sent)
                except Exception:
                    log.error('We got an error trying to send a message.')
              
                
        def recv(self):
            if self.client is not None and self.sock is not None:
                try:
                    mssg = self.client.recv(self.size)
                except Exception:
                    log.error('We got an error trying to read a message')
                return mssg
            
            
        def start(self):
            log.debug('Starting socket server')
            self.run()
            
            
        def stop(self):
            if self.sock != None:
                self.sock.close()
    
    
        def listen(self):
            self.sock.listen(10)
            log.debug('server now listening')
            self.client, addr = self.sock.accept()
            log.debug('Connected with ' + addr[0] + ':' + str(addr[1]))
            while 1:
                mssg = self.recv()
                if mssg:
                    self.rcvd = mssg 
                    self.onRecv(mssg)
            

def main(argv=sys.argv):
    '''Main method called by the eggsecutable.'''
    try:
        utils.vip_main(EnergyPlusAgent)
    except Exception as e:
        log.exception(e)


if __name__ == '__main__':
    # Entry point for script
    sys.exit(main())
