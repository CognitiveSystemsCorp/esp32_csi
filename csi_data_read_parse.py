#!/usr/bin/env python3
# -*-coding:utf-8-*-

# Copyright 2021 Espressif Systems (Shanghai) PTE LTD
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

# WARNING: we don't check for Python build-time dependencies until
# check_environment() function below. If possible, avoid importing
# any external libraries here - put in external script, or import in
# their specific function instead.

import sys
import csv
import json
import argparse
import numpy as np

import serial
from os import path
from io import StringIO

from PyQt5.Qt import *
from pyqtgraph import PlotWidget
from PyQt5 import QtCore
import pyqtgraph as pq

import threading
import time

# Reduce displayed waveforms to avoid display freezes
CSI_VAID_SUBCARRIER_INTERVAL = 1

# Remove invalid subcarriers
# secondary channel : below, HT, 40 MHz, non STBC, v, HT-LFT: 0~63, -64~-1, 384
csi_vaid_subcarrier_color = []
color_step = 255 // (28 // CSI_VAID_SUBCARRIER_INTERVAL + 1)

LEGACY_LLTF_MASK = np.ones(64, dtype=bool)
LEGACY_LLTF_MASK[:1] = False
LEGACY_LLTF_MASK[27:27+10] = False
LEGACY_LLTF_MASK[63:] = False


C6_MASK = np.ones(64, dtype=bool)
C6_MASK[:6] = False
C6_MASK[32:33] = False
C6_MASK[59:] = False


C5_MASK = np.ones(53, dtype=bool)
C5_MASK[25:27] = False



# LLTF: 52
csi_vaid_subcarrier_color += [(i * color_step, 0, 0) for i in range(1,  26 // CSI_VAID_SUBCARRIER_INTERVAL + 2)]
csi_vaid_subcarrier_color += [(0, i * color_step, 0) for i in range(1,  26 // CSI_VAID_SUBCARRIER_INTERVAL + 2)]

CSI_DATA_INDEX = 1  # buffer size
DATA_COLUMNS_NUM = 13

class csi_data_graphical_window(QWidget):
    def __init__(self):
        super().__init__()

        self.resize(1280, 720)
        self.plotWidget_ted = PlotWidget(self)
        self.plotWidget_ted.setGeometry(QtCore.QRect(0, 0, 1280, 720))

        self.csi_data_array = np.zeros(53)

        self.plotWidget_ted.setXRange(0, len(self.csi_data_array), padding=0)
        self.plotWidget_ted.setYRange(0, 65)
        #self.plotWidget_ted.addLegend()
        self.plotWidget_ted.setBackground('w')
        self.plotWidget_ted.setLabel('bottom', 'Carrier', units='')
        self.plotWidget_ted.setLabel('left', 'Amplitude', units='')
 

        self.curve_list = []
        for i in range(CSI_DATA_INDEX):
            curve = self.plotWidget_ted.plot(
                self.csi_data_array, name=str(i), pen=csi_vaid_subcarrier_color[i])
            self.curve_list.append(curve)

        self.timer = pq.QtCore.QTimer()
        self.timer.timeout.connect(self.update_data)
        self.timer.start(100)

    def update_data(self):
        self.curve_list[0].setData(self.csi_data_array)

    def closeEvent(self,event):
        print('Closing')
        self.subthread.running = False
        #self.subthread.terminate()
        self.subthread.wait()

def csi_data_read_parse(self, port: str, mat_writer):
    ser = serial.Serial(port=port, baudrate=115200,
                        bytesize=8, parity='N', stopbits=1)
    if ser.isOpen():
        print("open success", mat_writer)
    else:
        print("open failed", mat_writer)
        return
    
    self.running = True

    csis = []
    while self.running:
        strings = str(ser.readline())
        if not strings:
            break
        strings = strings.lstrip('b\'').rstrip('\\r\\n\'')
        index = strings.find('CSI_DATA')

        if index == -1:
            continue

        csv_reader = csv.reader(StringIO(strings))
        csi_data = next(csv_reader)

        if len(csi_data) != DATA_COLUMNS_NUM:
            print("element number is not equal")
            continue
        
        try:
            csi_raw_data = json.loads(csi_data[-1])
        except json.JSONDecodeError:
            print("data is incomplete")
            continue

        if len(csi_raw_data) != 128 and len(csi_raw_data) != 106 and len(csi_raw_data) != 384:
            print(f"element number is not equal: {len(csi_raw_data)}")
            continue

        x = np.array(csi_raw_data, dtype=np.float32)
        z = x.reshape(-1, 2).view(np.complex64)

        #print(csi_data[:-1])
        print('valid', int(csi_data[-2]), 'rssi', int(csi_data[3]), 'ch', int(csi_data[6]), 'ts', int(csi_data[7]))

        if len(z) == 64:
            x = np.squeeze(z[C6_MASK])[:26] #upper 26 sub-carriers are noise on C6
        else:
            x = np.squeeze(z[C5_MASK])

        if mat_writer is not None:
            csis.append(x)

        '''
        unwrapped = np.unwrap(np.angle(x))
        subcarrier_indices = np.arange(len(unwrapped))
        coeffs = np.polyfit(subcarrier_indices, unwrapped, 1)
        linear_fit = np.polyval(coeffs, subcarrier_indices)
        # Subtract the linear trend
        corrected_phase = unwrapped - linear_fit
        y = corrected_phase 
        self.window.plotWidget_ted.setYRange(-np.pi/2, np.pi/2)
        '''

        y = np.abs(x)

        y /= np.mean(y) #remove AGC effect
        self.window.plotWidget_ted.setYRange(0, 2, padding=0)

        self.window.plotWidget_ted.setXRange(0, len(y), padding=0)
        self.window.csi_data_array = y

    ser.close()
    if mat_writer is not None:
        import scipy.io
        scipy.io.savemat(mat_writer, dict(CSI=np.array(csis)))
    return


class SubThread (QThread):
    def __init__(self, window, serial_port, save_file_name):
        super().__init__()
        self.daemon = True
        self.serial_port = serial_port
        self.save_file_name = save_file_name
        self.window = window
    def run(self):
        csi_data_read_parse(self, self.serial_port, self.save_file_name)

    def __del__(self):        
        print('here')
        #self.log_file_fd.close()


if __name__ == '__main__':
    if sys.version_info < (3, 6):
        print(" Python version should >= 3.6")
        exit()

    parser = argparse.ArgumentParser(
        description="Read CSI data from serial port and display it graphically")
    parser.add_argument('-p', '--port', dest='port', action='store', required=True,
                        help="Serial port number of csv_recv device")
    parser.add_argument('-s', '--store', dest='store_file', action='store',
                        help="Save the data printed by the serial port to a file")

    args = parser.parse_args()
    serial_port = args.port
    file_name = args.store_file

    app = QApplication(sys.argv)



    window = csi_data_graphical_window()
    window.subthread = SubThread(window, serial_port, file_name)
    window.subthread.start()

    window.show()

    sys.exit(app.exec())
