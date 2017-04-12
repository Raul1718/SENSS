#!/usr/bin/python
# A simple example of a threaded TCP server in Python.
#
# Copyright (c) 2012 Benoit Sigoure  All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# - Redistributions of source code must retain the above copyright notice,
# this list of conditions and the following disclaimer.
# - Redistributions in binary form must reproduce the above copyright notice,
# this list of conditions and the following disclaimer in the documentation
# and/or other materials provided with the distribution.
# - Neither the name of the StumbleUpon nor the names of its contributors
# may be used to endorse or promote products derived from this software
# without specific prior written permission.
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

import SocketServer
import asyncore
import socket
import json
import pickle
from threading import Thread, Timer
import time
from collections import defaultdict, deque
import gc
from heapq import heappush, heappop
from copy import deepcopy


heap = []
current_data = {}
current_timestamp = 0
all_data = {}
closed_clients = []

reports_count = 29
new_start = False

'''
# Old Detection Module
def detect():
    global stats, curtime, lasttime

    while True:
        diff = curtime - lasttime
        print "curtime " + str(curtime) + " lasttime " + str(lasttime) + " diff" + str(diff)
        if (int(curtime - lasttime) > DETINT):
            print "Will detect "
            ot = curtime
            newstats = dict(stats)
            for t in newstats:
                if t > lasttime + DETINT and lasttime > 0:
                    continue
                for dst in stats[t]:
                    if (stats[t][dst] > 0):
                        print "attack on " + str(dst) + " at time " + str(t) + " stats " + str(stats[t][dst])
                del stats[t]
                lasttime = ot
        sleep(1)

'''


class RemoteClient(asyncore.dispatcher):
    def __init__(self, host, client_socket, address):
        asyncore.dispatcher.__init__(self, client_socket)
        self.host = host
        self.outbox = deque()
        self.name = None
        self.rb = ""

    def writable(self):
        ''' It has point to call handle_write only when there's something in outbox
            Having this method always returning true will cause 100% CPU usage
        '''
        return bool(self.outbox)

    def say(self, message):
        self.outbox.append(message)

    def handle_read(self):
        global reports_count, all_data, closed_clients, current_timestamp
        result = ""
        client_message = self.recv(999999999)
        # print "response"
        try:
            if self.rb != "":
                client_message = self.rb + client_message
                self.rb = client_message
            data = json.loads(client_message)
            # print "loaded"
            if self.name is None:
                self.name = data[0]['reader']
            if self.name not in all_data:
                all_data[self.name] = []
            # print len(data)
            for single_data in data:
                all_data[self.name].append((single_data['time'], single_data['destinations']))
            result = self.client_message_handle(data, reader_name=self.name, load_json=True)
            self.rb = ""
        except ValueError as e:
            # print e
            client_message = client_message.strip()
            if client_message == "close" or client_message == "":
                closed_clients.append(self.name)
                print "close"
                # reports_count -= 1
                result = self.client_message_handle("close", force_get_next=True)
            elif client_message == "Done":
                self.host.all_close()
                raise asyncore.ExitNow()
            elif len(client_message) >= 20:
                if self.rb == "":
                    self.rb += client_message
            """
            else:
                self.host.all_close()
                result = self.client_message_handle(client_message)
            """
        if result == "all_close":
            self.host.all_close()
            self.host.consume_time_exceed_timestamps(current_timestamp)
            print result
            return
        # print result
        self.host.broadcast(result)

    def handle_write(self):
        if not self.outbox:
            return
        message = self.outbox.popleft()
        message += "\t"
        self.send(message)

    def client_message_handle(self, data, reader_name=None, load_json=False, force_get_next=False):
        global stats, new_start, heap, reports_count, current_data, current_timestamp, all_data, closed_clients
        try:
            if new_start:
                print "new"
                new_start = False
        except:
            pass
        # new_start = False
        if not load_json and not force_get_next:
            # TODO: There might be some timestamps in previous and next log file iterations
            print data
            print "not load json"
            self.host.consume_time_exceed_timestamps(current_timestamp)
            if data == "Done":
                print "all done"
            print "done"
            new_start = True
            return ""
        if not force_get_next:
            # prev_dict_save = int(time.time())
            t = int(all_data[reader_name][0][0])
            heappush(heap, (t, reader_name))
            current_data[reader_name] = all_data[reader_name][0][1]
            del all_data[reader_name][0]
        if len(heap) < reports_count and data != "close":
            print "reader: " + str(reader_name)
            print len(all_data[reader_name])
            return ""

        while True:
            try:
                heap_element = heappop(heap)
            except:
                remaining_flows_flag = False
                for reader in all_data:
                    if len(all_data[reader]) > 0:
                        t = int(all_data[reader][0][0])
                        heappush(heap, (t, reader))
                        current_data[reader] = all_data[reader][0][1]
                        del all_data[reader][0]
                        remaining_flows_flag = True
                if not remaining_flows_flag:
                    self.host.consume_time_exceed_timestamps(current_timestamp)
                    print closed_clients
                    return ""
                else:
                    heap_element = heappop(heap)
            if current_timestamp == 0:
                current_timestamp = heap_element[0]
            elif heap_element[0] > current_timestamp:
                # detect attacks
                self.host.consume_time_exceed_timestamps(current_timestamp)
                current_timestamp = heap_element[0]
            data = current_data[heap_element[1]]
            if current_timestamp not in stats:
                stats[current_timestamp] = dict()

            for dst in data:
                if dst in stats[current_timestamp]:
                    stats[current_timestamp][dst][0] += data[dst]['q']
                    stats[current_timestamp][dst][1] += data[dst]['p']
                else:
                    stats[current_timestamp][dst] = [data[dst]['q'], data[dst]['p']]

            try:
                t = int(all_data[heap_element[1]][0][0])
                heappush(heap, (t, heap_element[1]))
                current_data[heap_element[1]] = all_data[heap_element[1]][0][1]
                del all_data[heap_element[1]][0]
            except IndexError as e:
                continue

            if len(all_data[heap_element[1]]) <= 90:
                if heap_element[1] not in closed_clients:
                    return heap_element[1]
                else:
                    if len(all_data[heap_element[1]]) > 0:
                        t = int(all_data[heap_element[1]][0][0])
                        heappush(heap, (t, heap_element[1]))
                        current_data[heap_element[1]] = all_data[heap_element[1]][0][1]
                        del all_data[heap_element[1]][0]
                    else:
                        reports_count -= 1
                        if reports_count == 0:
                            return "all_close"


class Host(asyncore.dispatcher):
    def __init__(self, address=('localhost', 4242)):
        asyncore.dispatcher.__init__(self)
        self.create_socket(socket.AF_INET, socket.SOCK_STREAM)
        self.bind(address)
        self.listen(29)
        self.remote_clients = []
        self.hour_count = 0
        self.attack_fh = open("attacks-" + str(self.hour_count), "a", buffering=0)

    def handle_accept(self):
        client_socket, addr = self.accept()  # For the remote client.
        self.remote_clients.append(RemoteClient(self, client_socket, addr))

    def handle_read(self):
        pass

    def writable(self):
        ''' It has point to call handle_write only when there's something in outbox
            Having this method always returning true will cause 100% CPU usage
        '''
        return False

    def readable(self):
        return False

    def handle_close(self):
        global reports_count
        print "closed"
        reports_count -= 1

    def broadcast(self, message):
        for remote_client in self.remote_clients:
            remote_client.say(message)

    def all_close(self):
        global heap, current_data, current_timestamp, all_data, closed_clients, reports_count, new_start
        self.remote_clients = []
        self.attack_fh.close()
        self.hour_count += 1
        self.attack_fh = open("attacks-" + str(self.hour_count), "a", buffering=0)

        # Initialize global variables
        heap = []
        current_data = {}
        current_timestamp = 0
        all_data = {}
        closed_clients = []
        reports_count = 29
        new_start = False

    def consume_time_exceed_timestamps(self, timestamp):
        global stats
        print "t: " + str(timestamp)
        if timestamp in stats:
            for dst in stats[timestamp]:
                req_rep = stats[timestamp][dst][0] - stats[timestamp][dst][1]
                if req_rep >= 10:
                    # timestamp dst req rep flow_count
                    self.attack_fh.write(str(timestamp) + "\t" + dst + "\t" + str(stats[timestamp][dst][0]) + "\t" + str(
                        stats[timestamp][dst][1]) + "\t" + str(req_rep) + "\n")
            del stats[timestamp]


"""
def dump_dictionary(file_name, index):
    global stats, timestamps, attacks, file_count
    with open(file_name, 'wb') as handle:
        pickle.dump(attacks, handle)
        del attacks
        gc.collect()
        attacks = []
"""

"""
def save_dict(force=False):
    global stats, prev_dict_save, file_count, client_arr, attacks, timestamps, min_timestamp, last_timestamp_recd, save_lock, file_count1
    if not force:
        Timer(10.0, save_dict).start()
    if save_lock:
        return
    save_lock = True
    print len(attacks)
    save_dict.backlog_time += 1
    # print "arr: " + str(len(client_arr))
    if len(attacks) > 500000 or force or save_dict.backlog_time >= 5:
        print "inside"
        # prev_dict_save = int(time.time())
        file_name = "attack-dump-" + str(file_count1) + ".pickle"
        dump_dictionary(file_name, None)
        file_count1 += 1
        save_dict.backlog_time = 0
        print "saved"
    save_lock = False


save_dict.backlog_time = 0
"""

stats = dict()


def main():
    # save_dict()
    # consume_completed_timestamps()
    # consume_time_exceed_timestamps()
    server = Host(address=('localhost', 4242))
    try:
        asyncore.loop()
    except asyncore.ExitNow, e:
        pass
    print "all complete"


if __name__ == "__main__":
    main()
