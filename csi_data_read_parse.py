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

from scipy.signal import butter, filtfilt

def snr_simple(csi):
    if np.iscomplexobj(csi):
        csi = np.abs(csi)
    else:
        csi = np.asarray(csi, dtype=np.float64)

    # Design Butterworth filter (4th order, cutoff 0.3)
    b, a = butter(4, 0.3)
    csi_filt = filtfilt(b, a, csi)

    # Calculate signal and noise power
    s = np.sum(csi_filt)
    n = np.sum(np.abs(csi_filt - csi))

    # Compute SNR in dB
    csi_snr = 0.0
    if n != 0:
        csi_snr = 10 * np.log10(s / n)
    return float(csi_snr)



def snr_cir_delay_domain(csi, max_delay_taps=6):
    """
    Computes CSI SNR using CIR delay-domain noise estimation (Algorithm 1).
    Applies frequency-domain Hann windowing to suppress sinc leakage,
    aligns the CIR peak circularly to index 0 to eliminate packet delay offsets,
    and estimates noise variance robustly from delay taps beyond the channel
    delay spread using an exponential-distribution median estimator.
    """
    csi_arr = np.asarray(csi)
    n_subcarriers = len(csi_arr)
    if n_subcarriers < 8:
        return 0.0

    # 1. Apply Hann window in frequency domain to suppress Dirichlet sinc leakage
    win = np.hanning(n_subcarriers)
    win_scale = float(np.mean(win ** 2))
    cir_win = np.fft.ifft(csi_arr * win)
    cir_power_win = (np.abs(cir_win) ** 2) / win_scale

    # 2. Circularly align CIR peak to index 0
    peak_idx = int(np.argmax(cir_power_win))
    cir_aligned = np.roll(cir_power_win, -peak_idx)

    # 3. Guard window around peak (symmetric positive and negative delay margin)
    guard_taps = max(2, min(max_delay_taps, n_subcarriers // 4))
    noise_taps = cir_aligned[guard_taps:n_subcarriers - guard_taps]

    if len(noise_taps) == 0:
        return 0.0

    # 4. Robust noise power estimation for complex Gaussian noise (Exp distribution)
    p_noise = float(np.median(noise_taps) / np.log(2.0))
    p_total = float(np.mean(cir_power_win))

    # 5. Compute signal power and SNR in dB
    p_signal = max(p_total - p_noise, 1e-12)
    p_noise = max(p_noise, 1e-12)

    return float(10.0 * np.log10(p_signal / p_noise))


def snr_mad_robust(csi):
    """
    Computes CSI SNR using Median Absolute Deviation (MAD) of adjacent
    subcarrier differences (Algorithm 3). Robust to multipath shape and
    free of filter boundary distortion.
    """
    if np.iscomplexobj(csi):
        y = np.abs(csi)
    else:
        y = np.asarray(csi, dtype=np.float64)
    if len(y) < 4:
        return 0.0

    # First-order difference across adjacent subcarriers
    diff = np.diff(y)
    mad = np.median(np.abs(diff - np.median(diff)))
    sigma_n = mad / (0.6745 * np.sqrt(2.0))
    p_noise = max(float(sigma_n ** 2), 1e-12)

    p_total = float(np.mean(y ** 2))
    p_signal = max(p_total - p_noise, 1e-12)

    return float(10.0 * np.log10(p_signal / p_noise))


SNR_ALGORITHMS = {
    'simple': snr_simple,
    'cir': snr_cir_delay_domain,
    'mad': snr_mad_robust,
}





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





# LLTF: 52
csi_vaid_subcarrier_color += [(i * color_step, 0, 0) for i in range(1,  26 // CSI_VAID_SUBCARRIER_INTERVAL + 2)]
csi_vaid_subcarrier_color += [(0, i * color_step, 0) for i in range(1,  26 // CSI_VAID_SUBCARRIER_INTERVAL + 2)]

CSI_DATA_INDEX = 1  # buffer size
DATA_COLUMNS_NUM = 13

class csi_data_graphical_window(QMainWindow):
    def __init__(self, snr_algo='simple'):
        super().__init__()

        self.snr_algo = snr_algo
        self.snr_fn = SNR_ALGORITHMS.get(snr_algo, snr_simple)

        self.setWindowTitle(f"ESP32 CSI Viewer (SNR: {self.snr_algo})")
        self.resize(800, 900)

        self.graphWidget = pq.GraphicsLayoutWidget()
        self.setCentralWidget(self.graphWidget)

        self.mac_plots = {}
        self.max_plots = 4
        self.latest_data = {}

        self.timer = pq.QtCore.QTimer()
        self.timer.timeout.connect(self.update_data)
        self.timer.start(100)

    def get_or_create_plot(self, mac):
        if mac in self.mac_plots:
            return self.mac_plots[mac]
            
        if len(self.mac_plots) >= self.max_plots:
            return None
            
        plot = pq.PlotItem(title=f"ESP32 - MAC: {mac}")
        plot.setLabel('bottom', 'Subcarriers')
        plot.setLabel('left', 'Amplitude')
        plot.showGrid(x=True, y=True)
        
        plot_data = {
            'plot': plot,
            'curves': [],
            'snr_history': [],
            'y_min': float('inf'),
            'y_max': float('-inf'),
            'history_size': 20,
            'packet_count': 0,
            'last_time': time.time(),
            'rate': 0.0
        }
        
        self.mac_plots[mac] = plot_data

        # Rebuild layout in sorted MAC order
        self.graphWidget.clear()
        sorted_macs = sorted(self.mac_plots.keys())
        for i, m in enumerate(sorted_macs):
            if i > 0:
                self.graphWidget.nextRow()
            self.graphWidget.addItem(self.mac_plots[m]['plot'])

        return plot_data

    def update_data(self):
        for mac, data_info in list(self.latest_data.items()):
            y = data_info['y']
            x = data_info.get('x', y)

            # Algorithm 1 (CIR) operates best on complex CFR; others on amplitude y
            csi_input = x if self.snr_algo == 'cir' else y
            snr = self.snr_fn(csi_input)
            rssi = data_info['rssi']
            ch = data_info['ch']
            vld = data_info['vld']
            
            if np.all(y == 0):
                continue

            plot_data = self.get_or_create_plot(mac)
            if not plot_data:
                continue

            plot_data['packet_count'] += 1
            current_time = time.time()
            elapsed = current_time - plot_data['last_time']
            if elapsed >= 1.0:
                plot_data['rate'] = plot_data['packet_count'] / elapsed
                plot_data['packet_count'] = 0
                plot_data['last_time'] = current_time
            
            history_size = plot_data['history_size']
            plot_data.setdefault('snr_history', []).append(snr)
            if len(plot_data['snr_history']) > history_size:
                plot_data['snr_history'].pop(0)
            avg_snr = float(np.mean(plot_data['snr_history']))

            rate_str = f"{plot_data['rate']:.1f}"
            plot_data['plot'].setTitle(f"MAC: {mac} (RSSI: {rssi} dBm, Ch: {ch}, Valid: {vld}, Rate: {rate_str} Hz Avg SNR= {avg_snr:.1f} dB)")
            plot_data['plot'].setXRange(0, len(y), padding=0)

            H_min = float(np.min(y))
            H_max = float(np.max(y))
            update_range = False
            
            if plot_data['y_min'] == float('inf') or H_min < plot_data['y_min']:
                plot_data['y_min'] = H_min
                update_range = True
            else:
                plot_data['y_min'] = 0.99 * plot_data['y_min'] + 0.01 * H_min
                update_range = True
                
            if plot_data['y_max'] == float('-inf') or H_max > plot_data['y_max']:
                plot_data['y_max'] = H_max
                update_range = True
            else:
                plot_data['y_max'] = 0.99 * plot_data['y_max'] + 0.01 * H_max
                update_range = True
                
            if update_range:
                padding = (plot_data['y_max'] - plot_data['y_min']) * 0.05 if plot_data['y_max'] > plot_data['y_min'] else 0.1
                plot_data['plot'].setYRange(plot_data['y_min'] - padding, plot_data['y_max'] + padding)

            curves = plot_data['curves']
            history_size = plot_data['history_size']
            if not curves:
                base_color = pq.intColor(0, hues=1, values=1, maxValue=255)
                for h in range(history_size):
                    alpha = int(255 * (1.0 - h / history_size))
                    color = pq.mkColor(base_color)
                    color.setAlpha(alpha)
                    curve = plot_data['plot'].plot(np.zeros(len(y)), pen=pq.mkPen(color, width=2 if h == 0 else 1))
                    curves.append(curve)

            for h in range(history_size - 1, 0, -1):
                x_data, y_data = curves[h-1].getData()
                if y_data is not None:
                    curves[h].setData(y_data)
            curves[0].setData(y)
            
        self.latest_data.clear()

    def closeEvent(self,event):
        print('Closing')
        self.subthread.running = False
        #self.subthread.terminate()
        self.subthread.wait()

def sign_extend(value : np.int16, bits : int):
    sign_bit = 1 << (bits - 1)
    return (value & (sign_bit - 1)) - (value & sign_bit)

def parse_12bit(raw):
    lraw = len(raw)
    data = np.empty(lraw, dtype=np.float32)
    for i in range(0, lraw, 2):
        data[i] = sign_extend(raw[i], 12)
        data[i+1 ]= sign_extend(raw[i+1],12)
    data = data.view(np.complex64)
    return data



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

        if index != 0:
            continue

        csv_reader = csv.reader(StringIO(strings))
        csi_data = next(csv_reader)

        if len(csi_data) != 16:
            print('len', len(csi_data))
            continue


        try:
            csi_raw_data = json.loads(csi_data[-1])
        except json.JSONDecodeError:
            print("data is incomplete")
            continue

        if len(csi_raw_data) != 106:
            x = np.array(csi_raw_data, dtype=np.float32)
            z = x.reshape(-1, 2).view(np.complex64)
        else:
            buf = np.array(csi_raw_data, dtype=np.int8)
            raw = np.frombuffer(buf, count=52, dtype='<h')
            z = parse_12bit(raw)

        try:
            valid = int(csi_data[2])
            ch = float(csi_data[8])
            tx_mac = csi_data[4]
            rssi = int(csi_data[5])
        except Exception as e:
            print(e)
            continue

        if len(z) == 64:
            x = np.squeeze(z[C6_MASK])
            if valid == 0:
                x = x[:26] #upper 26 sub-carriers are noise on C6
        else:
            x = np.squeeze(z)

        if mat_writer is not None:
            inx = np.array(csi_raw_data, dtype=np.int8)
            csis.append(inx) # x

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

        y = np.abs(x) # * comp

        #y = y / np.mean(y)

        #y /= np.mean(y) #reduce AGC effect

        if not hasattr(self.window, 'latest_data'):
            self.window.latest_data = {}
            
        self.window.latest_data[tx_mac] = {
            'x': x,
            'y': y,
            'rssi': rssi,
            'ch': ch,
            'vld': valid 
        }

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
        pass
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
    parser.add_argument('--snr-algo', dest='snr_algo', default='cir',
                        choices=['simple', 'cir', 'mad'],
                        help="Algorithm for CSI SNR computation: 'simple' (default, snr_simple Butterworth filter), 'cir' (Algorithm 1, delay-domain IFFT), 'mad' (Algorithm 3, robust MAD)")

    args = parser.parse_args()
    serial_port = args.port
    file_name = args.store_file
    selected_algo = str(args.snr_algo).lower()

    app = QApplication(sys.argv)



    window = csi_data_graphical_window(snr_algo=selected_algo)
    window.subthread = SubThread(window, serial_port, file_name)
    window.subthread.start()

    window.show()

    sys.exit(app.exec())
