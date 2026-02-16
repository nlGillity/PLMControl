import pyqtgraph
import test_ui
import start_experiment_dialog
from PyQt5 import QtWidgets, QtCore, QtGui
from handlers.database_handler import Info, Instruments, Base
from handlers.instruments_handler import *
import os
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
import json
import pyvisa
from datetime import datetime, timedelta
import numpy as np
from handlers.mqtt_client import MQTTProducer
import time


class Plot:
    def __init__(self, canvas: pyqtgraph.GraphicsLayoutWidget, index: int, graph_size: int):
        self.index = index
        self.graph_size = graph_size
        self._time_axis = np.linspace((datetime.now() - timedelta(seconds=graph_size)).timestamp(), datetime.now().timestamp(), graph_size)
        self.plot_view = canvas.addPlot(row=index, col=0)
        self.plot_view.setAxisItems({'bottom': pyqtgraph.DateAxisItem()})
        self.plot_view.showGrid(x=True, y=True)
        self._curves = []
        self._data = []
        self._curve_idx = 0
    
    def create_curve(self, name):
        self._data.append(np.ones(self.graph_size))
        self._curves.append(self.plot_view.plot(
            self._time_axis,
            self._data[self._curve_idx],
            pen=pyqtgraph.mkPen(color=pyqtgraph.intColor(self._curve_idx + self.index + 3),
            name=name)))
        # TODO: proper legend
        # self.plot_view.addLegend()
        # self.plot_view.legend.setBrush(pyqtgraph.mkBrush(30, 30, 30, 200))
        # self.plot_view.legend.setFont(QtGui.QFont('Arial', 6))
        # self.plot_view.legend.labelTextColor(pyqtgraph.mkColor('w'))
        # self.plot_view.legend.layout.setSpacing(2)

        self._curve_idx += 1
        
    def update(self, timestamp, value):
        _x = np.delete(self._time_axis, 0)
        self._time_axis = np.append(_x, timestamp)
        for i in range(self._curve_idx):
            self._data[i] = np.delete(self._data[i], 0)
            if isinstance(value, list):
                self._data[i] = np.append(self._data[i], value[i])
            else:
                self._data[i] = np.append(self._data[i], value)
            self._curves[i].setData(self._time_axis, self._data[i])


def get_available_facilities() -> list:
    """ Получить список доступных установок """
    return json.load(open("config_paths.json", encoding = "utf-8")).keys()


def calc_cathode_temp(voltage, current, k):
    # Функция расчёта температуры катода
    # На входе получает напряжение и ток катода, а также величину k, которая определяется экспериментально
    # Передаёт температуру катода в кельвинах
    voltage = float(voltage)
    current = float(current)
    if current != 0.0 and voltage != 0.0:
        R = voltage / current
        ro = R * k
        T_K = round((0.084 * (ro ** 2)) + (17.56 * ro) + 62.557, 2)
        return T_K
    else:
        T_K = 0.0
        return T_K


class Reader(QtCore.QObject):
    reader_result = QtCore.pyqtSignal(dict, dict, float)

    def __init__(
        self,
        read_interval : float,
        sample        : SCPIInstrument, 
        discharge     : SCPIInstrument,  
        solenoid_1    : SCPIInstrument,
        solenoid_2    : SCPIInstrument, 
        cathode       : SCPIInstrument, 
        rrg           : RRGInstrument, 
        pressure_1    : VacuumeterERSTEVAK,
        pressure_2    : VacuumeterERSTEVAK, 
        pressure_3    : VacuumeterERSTEVAK,
        thermocouple  : NIDAQInstrument,
        k_value       : float,
        mqtt_configs  : dict
    ) -> None:

        QtCore.QObject.__init__(self)

        self.read_interval = read_interval
        self.k             = k_value

        self.client       = MQTTProducer(mqtt_configs)

        self.sample       = sample
        self.discharge    = discharge
        self.solenoid_1   = solenoid_1
        self.solenoid_2   = solenoid_2
        self.cathode      = cathode
        self.rrg          = rrg
        self.pressure_1   = pressure_1
        self.pressure_2   = pressure_2
        self.pressure_3   = pressure_3
        self.thermocouple = thermocouple

    def run(self) -> None:
        while True:
            start = time.perf_counter()
            delay = timedelta(milliseconds=self.read_interval)
            deadline = datetime.now() + delay 
            instrument_data   = {}
            thermocouple_data = {}
            
            # ----------------------- Publish the data --------------------------------
            start_sample = time.perf_counter()
            instrument_data.update({"sample_current": self.sample.get_current()})
            instrument_data.update({"sample_voltage": self.sample.get_voltage()})
            instrument_data.update({"discharge_current": self.discharge.get_current()})
            instrument_data.update({"discharge_voltage": self.discharge.get_voltage()})
            instrument_data.update({"discharge_power": self.discharge.get_power()})
            instrument_data.update({"solenoid_current_1": self.solenoid_1.get_current()})
            instrument_data.update({"solenoid_voltage_1": self.solenoid_1.get_voltage()})
            instrument_data.update({"solenoid_current_2": self.solenoid_2.get_current()})
            instrument_data.update({"solenoid_voltage_2": self.solenoid_2.get_voltage()})
            instrument_data.update({"solenoid_power_2": self.solenoid_2.get_power()})
            instrument_data.update({"cathode_current": self.cathode.get_current()})
            instrument_data.update({"cathode_voltage": self.cathode.get_voltage()})
            instrument_data.update({"cathode_power": self.cathode.get_power()})
            instrument_data.update({"T_cathode"          : calc_cathode_temp(
                                                                    voltage = self.cathode.get_voltage(), 
                                                                    current = self.cathode.get_current(), 
                                                                    k       = self.k)})
            instrument_data.update({"rrg_value": self.rrg.get_flow_inlet()})
            instrument_data.update({"pressure_1": self.pressure_1.return_value()})
            instrument_data.update({"pressure_2": self.pressure_2.return_value()})
            instrument_data.update({"pressure_3": self.pressure_3.return_value()})
            thermocouple_data_raw = self.thermocouple.read_thermocouple()
            for i in range(len(thermocouple_data_raw)):
                thermocouple_data.update({f"CH{i}": thermocouple_data_raw[i]})
        
            timestamp = datetime.now().timestamp()
            self.reader_result.emit(instrument_data, thermocouple_data, timestamp)

            self.client.connect()

            for topic, value in instrument_data.items():
                self.client.publish(value, f"instruments/{topic}")

            for topic, value in thermocouple_data.items():
                self.client.publish(value, f"thermocouples/{topic}")
            
            self.client.publish(timestamp, "timestamp")

            self.client.disconnect()
            # if datetime.now() < deadline:
            #     pass
            end = time.perf_counter()
            print(f"Target reader cycle: {round(float(self.read_interval*1e-3), 2)}, got {end - start}")

class PLMControl(QtWidgets.QMainWindow):

    def __init__(self):
        QtWidgets.QMainWindow.__init__(self)

        self.start_db_writing = False
        
        self._init_ui()
        self._init_writing_routine()
        self._init_instrument_ui()

        # Последующая инициализация всех устройств происходит в
        # self._init_main(), вызываемой после закрытия начального окна    

    def _init_writing_routine(self) -> None:
        self.currentTime = QtCore.QTime(00, 00, 00)
        self.timeFormat = '0'
        self.experiment_timer = QtCore.QTimer()
        self.experiment_timer.timeout.connect(self.set_experiment_timer)

    def _init_instrument_ui(self) -> None:
        self.ui_start.OK_button.clicked.connect(self.start_main_window)
        self.ui_main.push_record.clicked.connect(self.start_experiment)
        self.ui_main.push_stopRecord.clicked.connect(self.stop_experiment)

        self.ui_main.check_local_sample.stateChanged.connect(self.sample_local)
        self.ui_main.check_remote_sample.stateChanged.connect(self.sample_remote)
        self.ui_main.sample_start.stateChanged.connect(self.sample_remote_start)
        self.ui_main.sample_stop.stateChanged.connect(self.sample_remote_stop)
        self.ui_main.set_u_sample.editingFinished.connect(self.set_u_sample)
        self.ui_main.set_u_sample_slider.sliderReleased.connect(self.set_u_sample_slider)
        self.ui_main.set_i_sample.editingFinished.connect(self.set_i_sample)
        self.ui_main.set_i_sample_slider.sliderReleased.connect(self.set_i_sample_slider)

        self.ui_main.check_local_discharge.stateChanged.connect(self.discharge_local)
        self.ui_main.check_remote_discharge.stateChanged.connect(self.discharge_remote)
        self.ui_main.discharge_start.stateChanged.connect(self.discharge_remote_start)
        self.ui_main.discharge_stop.stateChanged.connect(self.discharge_remote_stop)
        self.ui_main.set_u_discharge.editingFinished.connect(self.set_u_discharge)
        self.ui_main.set_i_discharge.editingFinished.connect(self.set_i_discharge)
        self.ui_main.set_p_discharge.editingFinished.connect(self.set_p_discharge)
        self.ui_main.set_u_discharge_slider.sliderReleased.connect(self.set_u_discharge_slider)
        self.ui_main.set_i_discharge_slider.sliderReleased.connect(self.set_i_discharge_slider)
        self.ui_main.set_p_discharge_slider.sliderReleased.connect(self.set_p_discharge_slider)

        self.ui_main.check_local_solenoid_1.stateChanged.connect(self.solenoid_1_local)
        self.ui_main.check_remote_solenoid_1.stateChanged.connect(self.solenoid_1_remote)
        self.ui_main.solenoid_start_1.stateChanged.connect(self.solenoid_1_remote_start)
        self.ui_main.solenoid_stop_1.stateChanged.connect(self.solenoid_1_remote_stop)
        self.ui_main.set_u_solenoid_1.editingFinished.connect(self.set_u_solenoid_1)
        self.ui_main.set_u_solenoid_slider_1.sliderReleased.connect(self.set_u_solenoid_1_slider)
        self.ui_main.set_i_solenoid_1.editingFinished.connect(self.set_i_solenoid_1)
        self.ui_main.set_i_solenoid_slider_1.sliderReleased.connect(self.set_i_solenoid_slider_1)

        self.ui_main.check_local_solenoid_2.stateChanged.connect(self.solenoid_2_local)
        self.ui_main.check_remote_solenoid_2.setDisabled(False)
        self.ui_main.check_remote_solenoid_2.stateChanged.connect(self.solenoid_2_remote)
        self.ui_main.solenoid_start_2.stateChanged.connect(self.solenoid_2_remote_start)
        self.ui_main.solenoid_stop_2.stateChanged.connect(self.solenoid_2_remote_stop)
        self.ui_main.set_u_solenoid_2.editingFinished.connect(self.set_u_solenoid_2)
        self.ui_main.set_u_solenoid_slider_2.sliderReleased.connect(self.set_u_solenoid_2_slider)
        self.ui_main.set_i_solenoid_2.editingFinished.connect(self.set_i_solenoid_2)
        self.ui_main.set_i_solenoid_slider_2.sliderReleased.connect(self.set_i_solenoid_slider_2)
        self.ui_main.set_p_solenoid_2.editingFinished.connect(self.set_p_solenoid_2)
        self.ui_main.set_p_solenoid_slider_2.sliderReleased.connect(self.set_p_solenoid_slider_2)

        self.ui_main.check_local_cathode.stateChanged.connect(self.cathode_local)
        self.ui_main.check_remote_cathode.stateChanged.connect(self.cathode_remote)
        self.ui_main.cathode_start.stateChanged.connect(self.cathode_remote_start)
        self.ui_main.cathode_stop.stateChanged.connect(self.cathode_remote_stop)
        self.ui_main.set_u_cathode.editingFinished.connect(self.set_u_cathode)
        self.ui_main.set_i_cathode.editingFinished.connect(self.set_i_cathode)
        self.ui_main.set_p_cathode.editingFinished.connect(self.set_p_cathode)
        self.ui_main.set_u_cathode_slider.sliderReleased.connect(self.set_u_cathode_slider)
        self.ui_main.set_i_cathode_slider.sliderReleased.connect(self.set_i_cathode_slider)
        self.ui_main.set_p_cathode_slider.sliderReleased.connect(self.set_p_cathode_slider)

        self.ui_main.set_rrg.editingFinished.connect(self.set_rrg)
        self.ui_main.set_rrg_slider.sliderReleased.connect(self.set_rrg_slider)

        self.ui_main.set_rrg_state.currentIndexChanged.connect(self.set_rrg_state)

        self.ui_main.set_gas.currentTextChanged.connect(self.set_gas)

    def __del__(self):
        self.reading_thread.terminate()

    def _init_main(self) -> None:
        self._init_settings()
        self._init_instruments()

        self.reading_worker = Reader(read_interval=self.read_interval, sample=self.sample,
                                           discharge=self.discharge, solenoid_1=self.solenoid_1,
                                           solenoid_2=self.solenoid_2, cathode=self.cathode,
                                           rrg=self.rrg, pressure_1=self.pressure_1,
                                           pressure_2=self.pressure_2, pressure_3=self.pressure_3, 
                                           thermocouple=self.thermocouple, k_value=self.k,
                                           mqtt_configs=self.mqtt_configs)
        
        self.reading_thread = QtCore.QThread()
        self.reading_worker.moveToThread(self.reading_thread)
        self.reading_thread.started.connect(self.reading_worker.run)
        self.reading_worker.reader_result.connect(self.get_values)
        self.reading_thread.start()

        self.init_graphs()

        self.x_instruments = [datetime.now().timestamp() - self.thermocouple_array_size + i for i in range(self.thermocouple_array_size)]

    def _init_ui(self):
        self.ui_main = test_ui.Ui_MainWindow()
        self.ui_mainwindow = QtWidgets.QMainWindow()
        self.ui_main.setupUi(self.ui_mainwindow)
        self.ui_start = start_experiment_dialog.Ui_Dialog()
        self.ui_start_dialog = QtWidgets.QDialog()
        self.ui_start.setupUi(self.ui_start_dialog)

    def start_main_window(self):
        self.ui_start_dialog.close()
        self._init_main()

        path = self.config['Path_to_write']
        try:
            os.mkdir(path)
        except OSError:
            print("Directory %s already exist" % path)
        else:
            print("Successfully created the directory %s" % path)

        name = QtCore.QDateTime.currentDateTime().toString('dd-MM-yyyy') + '_' + self.ui_start.filename.text()
        self.create_database(name, path)
        info = Info(
                    date=QtCore.QDateTime.currentDateTime().toString('dd.MM.yyyy'),
                    project=self.ui_start.project.text(),
                    facility=self.ui_start.facility.currentText(),
                    sample=self.ui_start.sample.text(),
                    description=self.ui_start.description.toPlainText(),
                    # TODO: remove that fields from database schema
                    spectroscopy=None,
                    mass_spectrum=None,
                    probe=None
                    )
        self.session.add(info)
        self.session.commit()
        self.ui_mainwindow.show()

    def create_database(self, name, path):
        engine = create_engine(f'sqlite:///{path}/{name}.db')
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        self.session = Session()

    def start_experiment(self):
        self.experiment_timer.start(1000)
        self.start_db_writing = True

    def stop_experiment(self):
        self.experiment_timer.stop()
        self.start_db_writing = False

    def set_experiment_timer(self):
        self.currentTime = self.currentTime.addSecs(1)
        self.timeFormat = self.currentTime.toString('hh:mm:ss')
        self.ui_main.set_timer.setText(self.timeFormat)

    def init_graphs(self):
        self.instrument_plots: dict[str, Plot] = {
            "sample_voltage": Plot(self.ui_main.sample_graph, 0, self.graph_size),
            "sample_current": Plot(self.ui_main.sample_graph, 1, self.graph_size),
            "discharge_voltage": Plot(self.ui_main.discharge_graph, 0, self.graph_size),
            "discharge_current": Plot(self.ui_main.discharge_graph, 1, self.graph_size),
            "discharge_power": Plot(self.ui_main.discharge_graph, 2, self.graph_size),
            "cathode_voltage": Plot(self.ui_main.cathode_graph, 0, self.graph_size),
            "cathode_current": Plot(self.ui_main.cathode_graph, 1, self.graph_size),
            "cathode_power": Plot(self.ui_main.cathode_graph, 2, self.graph_size),
            "T_cathode": Plot(self.ui_main.cathode_graph, 3, self.graph_size),
            "rrg_value": Plot(self.ui_main.pressure_graph, 0, self.graph_size),
            "pressure_1": Plot(self.ui_main.pressure_graph, 1, self.graph_size),
            "pressure_2": Plot(self.ui_main.pressure_graph, 2, self.graph_size),
            "pressure_3": Plot(self.ui_main.pressure_graph, 3, self.graph_size)
        }
        self.thermocouple_plots = Plot(self.ui_main.thermocouples_graph, 0, self.graph_size)

        for _, v in self.instrument_plots.items():
            v.create_curve(" ")
        for i in range(self.thermocouple_channel_stop - self.thermocouple_channel_start + 1):
            self.thermocouple_plots.create_curve(f"CH{i}")

    def _get_configs(self) -> dict:
        """ Получить конфигурационные данные, выбранной установки """

        # Считывание доступных установок и путей к конфигурационным файлам, принадлежащим им
        with open("config_paths.json", encoding = "utf-8") as file:
            config_paths = json.load(file)
 
        # Вычленение конфигурационных данных установки, выбранной в поле "Установка" стартового окна
        try:
            choosen_facility = self.ui_start.facility.currentText()
            path = config_paths[choosen_facility]
            print(f"Configuration for the '{choosen_facility}' facility")
            return json.load(open(path))
        except Exception as error:
            print(f"[!] Failed to choose the configuration file: {error}")
            return {}

    def _init_settings(self):
        self.config = self._get_configs()
        self.ui_main.set_gas.setDisabled(False)
        self.read_interval = float(self.config['Read_interval'])
        self.k = float(self.config['k_value'])
        self.graph_size = int(self.config['Graph_size'])
        self.mqtt_configs = self.config["mqtt"] if "mqtt" in self.config else {}

        self.sample_ip = self.config['sample_properties'][0]['IP']
        self.sample_connect = self.config['sample_properties'][0]['connection_type']
        
        self.discharge_ip = self.config['discharge_properties'][0]['IP']
        self.discharge_connect = self.config['discharge_properties'][0]['connection_type']
        
        self.solenoid_ip = self.config['solenoid_properties'][0]['IP']
        self.solenoid_connect = self.config['solenoid_properties'][0]['connection_type']
        
        self.solenoid_ip_2 = self.config['solenoid_properties'][1]['IP']
        self.solenoid_connect_2 = self.config['solenoid_properties'][1]['connection_type']
        
        self.cathode_ip = self.config['cathode_properties'][0]['IP']
        self.cathode_connect = self.config['cathode_properties'][0]['connection_type']

        self.thermocouple_path = self.config['Thermocouple'][0]['Path']
        self.thermocouple_array_size = int(self.config['Thermocouple'][0]['Array_size'])
        self.thermocouple_channel_start = int(self.config['Thermocouple'][0]['Channel_start'])
        self.thermocouple_channel_stop = int(self.config['Thermocouple'][0]['Channel_stop'])
        self.thermocouple_fast_read = float(self.config['Thermocouple'][0]['Fast_read'])

        self.rrg_config = self.config["RRG"][0]
        self.pressure_1_config = self.config['Pressure1'][0]
        self.pressure_2_config = self.config['Pressure2'][0]
        self.pressure_3_config = self.config['Pressure3'][0]

        self.ui_main.set_u_sample.setMinimum(-int(self.config['sample_properties'][0]['Voltage_limit']))
        self.ui_main.set_u_sample.setMaximum(int(self.config['sample_properties'][0]['Voltage_limit']))
        self.ui_main.set_u_sample_slider.setMinimum(-float(self.config['sample_properties'][0]['Voltage_limit']))
        self.ui_main.set_u_sample_slider.setMaximum(float(self.config['sample_properties'][0]['Voltage_limit']))
        self.ui_main.set_u_sample_slider.setInterval(0.01)
        self.ui_main.set_u_sample_slider.setValue(0)
        self.ui_main.set_i_sample.setMinimum(-int(self.config['sample_properties'][0]['Current_limit']))
        self.ui_main.set_i_sample.setMaximum(int(self.config['sample_properties'][0]['Current_limit']))
        self.ui_main.set_i_sample_slider.setMinimum(-float(self.config['sample_properties'][0]['Current_limit']))
        self.ui_main.set_i_sample_slider.setMaximum(float(self.config['sample_properties'][0]['Current_limit']))
        self.ui_main.set_i_sample_slider.setInterval(0.01)
        self.ui_main.set_i_sample_slider.setValue(0)

        self.ui_main.set_u_discharge.setMaximum(int(self.config['discharge_properties'][0]['Voltage_limit']))
        self.ui_main.set_u_discharge_slider.setMaximum(int(self.config['discharge_properties'][0]['Voltage_limit']))
        self.ui_main.set_u_discharge_slider.setInterval(0.01)
        self.ui_main.set_i_discharge.setMaximum(int(self.config['discharge_properties'][0]['Current_limit']))
        self.ui_main.set_i_discharge_slider.setMaximum(int(self.config['discharge_properties'][0]['Current_limit']))
        self.ui_main.set_i_discharge_slider.setInterval(0.01)
        self.ui_main.set_p_discharge.setMaximum(int(self.config['discharge_properties'][0]['Power_limit']))
        self.ui_main.set_p_discharge_slider.setMaximum(int(self.config['discharge_properties'][0]['Power_limit']))
        self.ui_main.set_p_discharge_slider.setInterval(0.01)

        self.ui_main.set_u_solenoid_1.setMaximum(int(self.config['solenoid_properties'][0]['Voltage_limit']))
        self.ui_main.set_u_solenoid_slider_1.setMaximum(int(self.config['solenoid_properties'][0]['Voltage_limit']))
        self.ui_main.set_u_solenoid_slider_1.setInterval(0.01)
        self.ui_main.set_i_solenoid_1.setMaximum(int(self.config['solenoid_properties'][0]['Current_limit']))
        self.ui_main.set_i_solenoid_slider_1.setMaximum(int(self.config['solenoid_properties'][0]['Current_limit']))
        self.ui_main.set_i_solenoid_slider_1.setInterval(0.01)

        self.ui_main.set_u_solenoid_2.setMaximum(int(self.config['solenoid_properties'][1]['Voltage_limit']))
        self.ui_main.set_u_solenoid_slider_2.setMaximum(int(self.config['solenoid_properties'][1]['Voltage_limit']))
        self.ui_main.set_u_solenoid_slider_2.setInterval(0.01)
        self.ui_main.set_i_solenoid_2.setMaximum(int(self.config['solenoid_properties'][1]['Current_limit']))
        self.ui_main.set_i_solenoid_slider_2.setMaximum(int(self.config['solenoid_properties'][1]['Current_limit']))
        self.ui_main.set_i_solenoid_slider_2.setInterval(0.01)
        self.ui_main.set_p_solenoid_2.setMaximum(int(self.config['solenoid_properties'][1]['Power_limit']))
        self.ui_main.set_p_solenoid_slider_2.setMaximum(int(self.config['solenoid_properties'][1]['Power_limit']))
        self.ui_main.set_p_solenoid_slider_2.setInterval(0.01)

        self.ui_main.set_u_cathode.setMaximum(int(self.config['cathode_properties'][0]['Voltage_limit']))
        self.ui_main.set_u_cathode_slider.setMaximum(int(self.config['cathode_properties'][0]['Voltage_limit']))
        self.ui_main.set_u_cathode_slider.setInterval(0.01)
        self.ui_main.set_i_cathode.setMaximum(int(self.config['cathode_properties'][0]['Current_limit']))
        self.ui_main.set_i_cathode_slider.setMaximum(int(self.config['cathode_properties'][0]['Current_limit']))
        self.ui_main.set_i_cathode_slider.setInterval(0.01)
        self.ui_main.set_p_cathode.setMaximum(int(self.config['cathode_properties'][0]['Power_limit']))
        self.ui_main.set_p_cathode_slider.setMaximum(int(self.config['cathode_properties'][0]['Power_limit']))
        self.ui_main.set_p_cathode_slider.setInterval(0.01)

        self.ui_main.set_rrg.setMaximum(300)
        self.ui_main.set_rrg.setMinimum(0)
        self.ui_main.set_rrg_slider.setMaximum(300)
        self.ui_main.set_rrg_slider.setMinimum(0)

    def _init_instruments(self):
        print("Establishing connection with sensors...")
        self.rm = pyvisa.ResourceManager()
        self.sample = SCPIInstrument(self.rm, self.sample_connect, self.sample_ip, 5025, name='Sample')
        if not self.sample.isInitialized:
            self.ui_main.check_remote_sample.setDisabled(True)

        self.discharge = SCPIInstrument(self.rm, self.discharge_connect, self.discharge_ip, 0, name='Discharge')
        if not self.discharge.isInitialized:
            self.ui_main.check_remote_discharge.setDisabled(True)

        self.solenoid_1 = SCPIInstrument(self.rm, self.solenoid_connect, self.solenoid_ip, 5025, name='Solenoid')
        if not self.solenoid_1.isInitialized:
            self.ui_main.check_remote_solenoid_1.setDisabled(True)

        self.solenoid_2 = SCPIInstrument(self.rm, self.solenoid_connect_2, self.solenoid_ip_2, 0, name='Solenoid 2')
        if not self.solenoid_2.isInitialized:
            self.ui_main.check_remote_solenoid_2.setDisabled(True)

        self.cathode = SCPIInstrument(self.rm, self.cathode_connect, self.cathode_ip, 0, name='Cathode')
        if not self.cathode.isInitialized:
            self.ui_main.check_remote_cathode.setDisabled(True)

        self.rrg = RRGInstrument(self.rrg_config)
        if not self.rrg.isInitialized:
            self.ui_main.set_rrg_state.setDisabled(True)
        else:
            self.rrg.set_flow(0)
            self.ui_main.set_rrg_state.setCurrentIndex(1) # closed

        self.thermocouple = NIDAQInstrument(self.thermocouple_path, 'thermocouples', self.thermocouple_channel_start,
                                            self.thermocouple_channel_stop)
        self.thermocouple.create_multiple_thermocouples()
        for i in range(self.thermocouple_channel_stop - self.thermocouple_channel_start + 1):
            self.ui_main.thermocoples_table.insertRow(i)

        self.pressure_1 = VacuumeterERSTEVAK(self.pressure_1_config)
        self.pressure_2 = VacuumeterERSTEVAK(self.pressure_2_config)
        self.pressure_3 = VacuumeterERSTEVAK(self.pressure_3_config)

    def get_values(self, instruments: dict[str, float], thermocouples: dict[str, float], timestamp: float):
        sample_voltage = instruments['sample_voltage']
        sample_current = instruments['sample_current']
        discharge_voltage = instruments['discharge_voltage']
        discharge_current = instruments['discharge_current']
        discharge_power = instruments['discharge_power']
        solenoid_voltage_1 = instruments['solenoid_voltage_1']
        solenoid_current_1 = instruments['solenoid_current_1']
        solenoid_voltage_2 = instruments['solenoid_voltage_2']
        solenoid_current_2 = instruments['solenoid_current_2']
        solenoid_power_2 = instruments['solenoid_power_2']
        cathode_voltage = instruments['cathode_voltage']
        cathode_current = instruments['cathode_current']
        cathode_power = instruments['cathode_power']
        cathode_temp = instruments['T_cathode']
        gas_flow = instruments['rrg_value']
        pressure_1 = instruments['pressure_1']
        pressure_2 = instruments['pressure_2']
        pressure_3 = instruments['pressure_3']

        self.ui_main.u_sample_actual.setText(str(sample_voltage))
        self.ui_main.i_sample_actual.setText(str(sample_current))
        self.ui_main.u_discharge_actual.setText(str(discharge_voltage))
        self.ui_main.i_discharge_actual.setText(str(discharge_current))
        self.ui_main.p_discharge_actual.setText(str(discharge_power))
        self.ui_main.u_solenoid_actual_1.setText(str(solenoid_voltage_1))
        self.ui_main.u_solenoid_actual_2.setText(str(solenoid_voltage_2))
        self.ui_main.i_solenoid_actual_1.setText(str(solenoid_current_1))
        self.ui_main.i_solenoid_actual_2.setText(str(solenoid_current_2))
        self.ui_main.p_solenoid_actual_2.setText(str(solenoid_power_2))
        self.ui_main.u_cathode_actual.setText(str(cathode_voltage))
        self.ui_main.i_cathode_actual.setText(str(cathode_current))
        self.ui_main.p_cathode_actual.setText(str(cathode_power))
        self.ui_main.t_cathode_actual.setText(str(cathode_temp))
        self.ui_main.rrg_actual.setText(str(gas_flow))
        self.ui_main.p_1_actual.setText(str('%.2E' % pressure_1))
        self.ui_main.p_2_actual.setText(str('%.2E' % pressure_2))
        self.ui_main.p_3_actual.setText(str('%.2E' % pressure_3))
        i_k = 0
        for k, v in thermocouples.items():
            self.ui_main.thermocoples_table.setItem(i_k, 0, QtWidgets.QTableWidgetItem(str(k)))
            self.ui_main.thermocoples_table.setItem(i_k, 1, QtWidgets.QTableWidgetItem(str(round(v,2))))
            i_k += 1

        for key, plot in self.instrument_plots.items():
            plot.update(timestamp, instruments[key])
        self.thermocouple_plots.update(timestamp, [value for _, value in thermocouples.items()])

        if self.start_db_writing:
            commit = Instruments(
                # TODO: remove unnecessary fields in database schema
                time=None,
                time_experiment=None,
                timestamp_abs=str(timestamp),
                timestamp_experimental=None,
                instruments_values=json.dumps(instruments),
                thermocouples_values=json.dumps(thermocouples)
            )
            self.session.add(commit)
            self.session.commit()

    def sample_local(self):
        if self.ui_main.check_local_sample.isChecked():
            # self.sample.set_mode_local()
            print('SAMPLE: SYSTEM:LOCAL')
            self.ui_main.check_remote_sample.setDisabled(False)
            self.ui_main.check_remote_sample.setChecked(False)
            self.ui_main.check_local_sample.setDisabled(True)
            self.ui_main.set_i_sample.setDisabled(True)
            self.ui_main.set_i_sample_slider.setDisabled(True)
            self.ui_main.set_u_sample.setDisabled(True)
            self.ui_main.set_u_sample_slider.setDisabled(True)
            # self.ui_main.set_p_sample.setDisabled(True)
            self.ui_main.sample_start.setDisabled(True)
            self.ui_main.sample_stop.setDisabled(True)

    def sample_remote(self):
        if self.ui_main.check_remote_sample.isChecked():
            # self.sample.set_mode_remote()
            print('SAMPLE: SYSTEM:REMOTE')
            self.ui_main.check_local_sample.setDisabled(False)
            self.ui_main.check_local_sample.setChecked(False)
            self.ui_main.check_remote_sample.setDisabled(True)
            self.ui_main.set_i_sample.setDisabled(False)
            self.ui_main.set_i_sample_slider.setDisabled(False)
            self.ui_main.set_u_sample.setDisabled(False)
            self.ui_main.set_u_sample_slider.setDisabled(False)
            # self.ui_main.set_p_sample.setDisabled(False)
            self.ui_main.sample_start.setDisabled(False)
            self.ui_main.sample_stop.setDisabled(False)

    def sample_remote_start(self):
        if self.ui_main.sample_start.isChecked():
            self.ui_main.sample_stop.setChecked(False)
            self.ui_main.sample_start.setDisabled(True)
            self.sample.set_output_on()

    def sample_remote_stop(self):
        if self.ui_main.sample_stop.isChecked():
            self.ui_main.sample_start.setChecked(False)
            self.ui_main.sample_start.setDisabled(False)
            self.sample.set_output_off()

    def discharge_local(self):
        if self.ui_main.check_local_discharge.isChecked():
            self.discharge.set_mode_local()
            print('DISCHARGE: SYSTEM:LOCAL')
            self.ui_main.check_remote_discharge.setDisabled(False)
            self.ui_main.check_remote_discharge.setChecked(False)
            self.ui_main.check_local_discharge.setDisabled(True)
            self.ui_main.set_i_discharge.setDisabled(True)
            self.ui_main.set_i_discharge_slider.setDisabled(True)
            self.ui_main.set_u_discharge.setDisabled(True)
            self.ui_main.set_u_discharge_slider.setDisabled(True)
            self.ui_main.set_p_discharge.setDisabled(True)
            self.ui_main.set_p_discharge_slider.setDisabled(True)
            self.ui_main.discharge_start.setDisabled(True)
            self.ui_main.discharge_stop.setDisabled(True)

    def discharge_remote(self):
        if self.ui_main.check_remote_discharge.isChecked():
            self.discharge.set_mode_remote()
            print('DISCHARGE: SYSTEM:REMOTE')
            self.ui_main.check_local_discharge.setDisabled(False)
            self.ui_main.check_local_discharge.setChecked(False)
            self.ui_main.check_remote_discharge.setDisabled(True)
            self.ui_main.set_i_discharge.setDisabled(False)
            self.ui_main.set_i_discharge_slider.setDisabled(False)
            self.ui_main.set_u_discharge.setDisabled(False)
            self.ui_main.set_u_discharge_slider.setDisabled(False)
            self.ui_main.set_p_discharge.setDisabled(False)
            self.ui_main.set_p_discharge_slider.setDisabled(False)
            self.ui_main.discharge_start.setDisabled(False)
            self.ui_main.discharge_stop.setDisabled(False)

    def discharge_remote_start(self):
        if self.ui_main.discharge_start.isChecked():
            self.ui_main.discharge_stop.setChecked(False)
            self.ui_main.discharge_start.setDisabled(True)
            self.discharge.set_output_on()

    def discharge_remote_stop(self):
        if self.ui_main.discharge_stop.isChecked():
            self.ui_main.discharge_start.setChecked(False)
            self.ui_main.discharge_start.setDisabled(False)
            self.discharge.set_output_off()

    def solenoid_1_local(self):
        if self.ui_main.check_local_solenoid_1.isChecked():
            print('SOLENOID: SYSTEM:LOCAL')
            self.ui_main.check_remote_solenoid_1.setDisabled(False)
            self.ui_main.check_remote_solenoid_1.setChecked(False)
            self.ui_main.check_local_solenoid_1.setDisabled(True)
            self.ui_main.set_i_solenoid_1.setDisabled(True)
            self.ui_main.set_i_solenoid_slider_1.setDisabled(True)
            self.ui_main.set_u_solenoid_1.setDisabled(True)
            self.ui_main.set_u_solenoid_slider_1.setDisabled(True)
            self.ui_main.solenoid_start_1.setDisabled(True)
            self.ui_main.solenoid_stop_1.setDisabled(True)

    def solenoid_1_remote(self):
        if self.ui_main.check_remote_solenoid_1.isChecked():
            print('SOLENOID: SYSTEM:REMOTE')
            self.ui_main.check_local_solenoid_1.setDisabled(False)
            self.ui_main.check_local_solenoid_1.setChecked(False)
            self.ui_main.check_remote_solenoid_1.setDisabled(True)
            self.ui_main.set_u_solenoid_1.setDisabled(False)
            self.ui_main.set_u_solenoid_slider_1.setDisabled(False)
            self.ui_main.set_i_solenoid_1.setDisabled(False)
            self.ui_main.set_i_solenoid_slider_1.setDisabled(False)
            self.ui_main.solenoid_start_1.setDisabled(False)
            self.ui_main.solenoid_stop_1.setDisabled(False)

    def solenoid_1_remote_start(self):
        if self.ui_main.solenoid_start_1.isChecked():
            self.ui_main.solenoid_stop_1.setChecked(False)
            self.ui_main.solenoid_start_1.setDisabled(True)
            self.solenoid_1.set_output_on()

    def solenoid_1_remote_stop(self):
        if self.ui_main.solenoid_stop_1.isChecked():
            self.ui_main.solenoid_start_1.setChecked(False)
            self.ui_main.solenoid_start_1.setDisabled(False)
            self.solenoid_1.set_output_off()

    def solenoid_2_local(self):
        if self.ui_main.check_local_solenoid_2.isChecked():
            self.solenoid_2.set_mode_local()
            print('SOLENOID: SYSTEM:LOCAL')
            self.ui_main.check_remote_solenoid_2.setDisabled(False)
            self.ui_main.check_remote_solenoid_2.setChecked(False)
            self.ui_main.check_local_solenoid_2.setDisabled(True)
            self.ui_main.set_i_solenoid_2.setDisabled(True)
            self.ui_main.set_i_solenoid_slider_2.setDisabled(True)
            self.ui_main.set_u_solenoid_2.setDisabled(True)
            self.ui_main.set_u_solenoid_slider_2.setDisabled(True)
            self.ui_main.set_p_solenoid_2.setDisabled(True)
            self.ui_main.set_p_solenoid_slider_2.setDisabled(True)
            self.ui_main.solenoid_start_2.setDisabled(True)
            self.ui_main.solenoid_stop_2.setDisabled(True)

    def solenoid_2_remote(self):
        if self.ui_main.check_remote_solenoid_2.isChecked():
            print('SOLENOID: SYSTEM:REMOTE')
            self.solenoid_2.set_mode_remote()
            self.ui_main.check_local_solenoid_2.setDisabled(False)
            self.ui_main.check_local_solenoid_2.setChecked(False)
            self.ui_main.check_remote_solenoid_2.setDisabled(True)
            self.ui_main.set_u_solenoid_2.setDisabled(False)
            self.ui_main.set_u_solenoid_slider_2.setDisabled(False)
            self.ui_main.set_i_solenoid_2.setDisabled(False)
            self.ui_main.set_i_solenoid_slider_2.setDisabled(False)
            self.ui_main.set_p_solenoid_2.setDisabled(False)
            self.ui_main.set_p_solenoid_slider_2.setDisabled(False)
            self.ui_main.solenoid_start_2.setDisabled(False)
            self.ui_main.solenoid_stop_2.setDisabled(False)

    def solenoid_2_remote_start(self):
        if self.ui_main.solenoid_start_2.isChecked():
            self.ui_main.solenoid_stop_2.setChecked(False)
            self.ui_main.solenoid_start_2.setDisabled(True)
            self.solenoid_2.set_output_on()

    def solenoid_2_remote_stop(self):
        if self.ui_main.solenoid_stop_2.isChecked():
            self.ui_main.solenoid_start_2.setChecked(False)
            self.ui_main.solenoid_start_2.setDisabled(False)
            self.solenoid_2.set_output_off()

    def cathode_local(self):
        if self.ui_main.check_local_cathode.isChecked():
            self.cathode.set_mode_local()
            print('CATHODE: SYSTEM:LOCAL')
            self.ui_main.check_remote_cathode.setDisabled(False)
            self.ui_main.check_remote_cathode.setChecked(False)
            self.ui_main.check_local_cathode.setDisabled(True)
            self.ui_main.set_i_cathode.setDisabled(True)
            self.ui_main.set_i_cathode_slider.setDisabled(True)
            self.ui_main.set_u_cathode.setDisabled(True)
            self.ui_main.set_u_cathode_slider.setDisabled(True)
            self.ui_main.set_p_cathode.setDisabled(True)
            self.ui_main.set_p_cathode_slider.setDisabled(True)
            self.ui_main.cathode_start.setDisabled(True)
            self.ui_main.cathode_stop.setDisabled(True)

    def cathode_remote(self):
        if self.ui_main.check_remote_cathode.isChecked():
            self.cathode.set_mode_remote()
            print('CATHODE: SYSTEM:REMOTE')
            self.ui_main.check_local_cathode.setDisabled(False)
            self.ui_main.check_local_cathode.setChecked(False)
            self.ui_main.check_remote_cathode.setDisabled(True)
            self.ui_main.set_i_cathode.setDisabled(False)
            self.ui_main.set_i_cathode_slider.setDisabled(False)
            self.ui_main.set_u_cathode.setDisabled(False)
            self.ui_main.set_u_cathode_slider.setDisabled(False)
            self.ui_main.set_p_cathode.setDisabled(False)
            self.ui_main.set_p_cathode_slider.setDisabled(False)
            self.ui_main.cathode_start.setDisabled(False)
            self.ui_main.cathode_stop.setDisabled(False)

    def cathode_remote_start(self):
        if self.ui_main.cathode_start.isChecked():
            self.ui_main.cathode_stop.setChecked(False)
            self.cathode.set_output_on()

    def cathode_remote_stop(self):
        if self.ui_main.cathode_stop.isChecked():
            self.ui_main.cathode_start.setChecked(False)
            self.cathode.set_output_off()

    def set_u_sample(self):
        self.u_sample = round(self.ui_main.set_u_sample.value(), 2)
        self.ui_main.set_u_sample_slider.setValue(self.u_sample)
        self.sample.set_voltage(self.u_sample)

    def set_u_sample_slider(self):
        self.u_sample = round(self.ui_main.set_u_sample_slider.value(), 2)
        self.ui_main.set_u_sample.setValue(self.u_sample)
        self.sample.set_voltage(self.u_sample)

    def set_i_sample(self):
        self.i_sample = round(self.ui_main.set_i_sample.value(), 2)
        self.ui_main.set_i_sample_slider.setValue(self.i_sample)
        self.sample.set_current(self.i_sample)

    def set_i_sample_slider(self):
        self.i_sample = round(self.ui_main.set_i_sample_slider.value(), 2)
        self.ui_main.set_i_sample.setValue(self.i_sample)
        self.sample.set_current(self.i_sample)

    def set_u_discharge(self):
        self.u_discharge = round(self.ui_main.set_u_discharge.value(), 2)
        self.ui_main.set_u_discharge_slider.setValue(self.u_discharge)
        self.discharge.set_voltage(self.u_discharge)

    def set_u_discharge_slider(self):
        self.u_discharge = round(self.ui_main.set_u_discharge_slider.value(), 2)
        self.ui_main.set_u_discharge.setValue(self.u_discharge)
        self.discharge.set_voltage(self.u_discharge)

    def set_i_discharge(self):
        self.i_discharge = round(self.ui_main.set_i_discharge.value(), 2)
        self.ui_main.set_i_discharge_slider.setValue(self.i_discharge)
        self.discharge.set_current(self.i_discharge)

    def set_i_discharge_slider(self):
        self.i_discharge = round(self.ui_main.set_i_discharge_slider.value(), 2)
        self.ui_main.set_i_discharge.setValue(self.i_discharge)
        self.discharge.set_current(self.i_discharge)

    def set_p_discharge(self):
        self.p_discharge = round(self.ui_main.set_p_discharge.value(), 2)
        self.ui_main.set_p_discharge_slider.setValue(self.p_discharge)
        self.discharge.set_power(self.p_discharge)

    def set_p_discharge_slider(self):
        self.p_discharge = round(self.ui_main.set_p_discharge_slider.value(), 2)
        self.ui_main.set_p_discharge.setValue(self.p_discharge)
        self.discharge.set_power(self.p_discharge)

    def set_u_solenoid_1(self):
        self.u_solenoid_1 = round(self.ui_main.set_u_solenoid_1.value(), 2)
        self.ui_main.set_u_solenoid_slider_1.setValue(self.u_solenoid_1)
        self.solenoid_1.set_voltage(self.u_solenoid_1)

    def set_u_solenoid_1_slider(self):
        self.u_solenoid_1 = round(self.ui_main.set_u_solenoid_slider_1.value(), 2)
        self.ui_main.set_u_solenoid_1.setValue(self.u_solenoid_1)
        self.solenoid_1.set_voltage(self.u_solenoid_1)

    def set_i_solenoid_1(self):
        self.i_solenoid_1 = round(self.ui_main.set_i_solenoid_1.value(), 2)
        self.ui_main.set_i_solenoid_slider_1.setValue(self.i_solenoid_1)
        self.solenoid_1.set_current(self.i_solenoid_1)

    def set_i_solenoid_slider_1(self):
        self.i_solenoid_1 = round(self.ui_main.set_i_solenoid_slider_1.value(), 2)
        self.ui_main.set_i_solenoid_1.setValue(self.i_solenoid_1)
        self.solenoid_1.set_current(self.i_solenoid_1)

    def set_u_solenoid_2(self):
        self.u_solenoid_2 = round(self.ui_main.set_u_solenoid_2.value(), 2)
        self.ui_main.set_u_solenoid_slider_2.setValue(self.u_solenoid_2)
        self.solenoid_2.set_voltage(self.u_solenoid_2)

    def set_u_solenoid_2_slider(self):
        self.u_solenoid_2 = round(self.ui_main.set_u_solenoid_slider_2.value(), 2)
        self.ui_main.set_u_solenoid_2.setValue(self.u_solenoid_2)
        self.solenoid_2.set_voltage(self.u_solenoid_2)

    def set_i_solenoid_2(self):
        self.i_solenoid_2 = round(self.ui_main.set_i_solenoid_2.value(), 2)
        self.ui_main.set_i_solenoid_slider_2.setValue(self.i_solenoid_2)
        self.solenoid_2.set_current(self.i_solenoid_2)

    def set_i_solenoid_slider_2(self):
        self.i_solenoid_2 = round(self.ui_main.set_i_solenoid_slider_2.value(), 2)
        self.ui_main.set_i_solenoid_2.setValue(self.i_solenoid_2)
        self.solenoid_2.set_current(self.i_solenoid_2)
    
    def set_p_solenoid_2(self):
        self.p_solenoid_2 = round(self.ui_main.set_p_solenoid_2.value(), 2)
        self.ui_main.set_p_solenoid_slider_2.setValue(self.p_solenoid_2)
        self.solenoid_2.set_power(self.p_solenoid_2)
    
    def set_p_solenoid_slider_2(self):
        self.p_solenoid_2 = round(self.ui_main.set_p_solenoid_slider_2.value(), 2)
        self.ui_main.set_i_solenoid_2.setValue(self.p_solenoid_2)
        self.solenoid_2.set_power(self.p_solenoid_2)

    def set_u_cathode(self):
        self.u_cathode = round(self.ui_main.set_u_cathode.value(), 2)
        self.ui_main.set_u_cathode_slider.setValue(self.u_cathode)
        self.cathode.set_voltage(self.u_cathode)

    def set_u_cathode_slider(self):
        self.u_cathode = round(self.ui_main.set_u_cathode_slider.value(), 2)
        self.ui_main.set_u_cathode.setValue(self.u_cathode)
        self.cathode.set_voltage(self.u_cathode)

    def set_i_cathode(self):
        self.i_cathode = round(self.ui_main.set_i_cathode.value(), 2)
        self.ui_main.set_i_cathode_slider.setValue(self.i_cathode)
        self.cathode.set_current(self.i_cathode)

    def set_i_cathode_slider(self):
        self.i_cathode = round(self.ui_main.set_i_cathode_slider.value(), 2)
        self.ui_main.set_i_cathode.setValue(self.i_cathode)
        self.cathode.set_current(self.i_cathode)

    def set_p_cathode(self):
        self.p_cathode = round(self.ui_main.set_p_cathode.value(), 2)
        self.ui_main.set_p_cathode_slider.setValue(self.p_cathode)
        self.cathode.set_power(self.p_cathode)

    def set_p_cathode_slider(self):
        self.p_cathode = round(self.ui_main.set_p_cathode_slider.value(), 2)
        self.ui_main.set_p_cathode.setValue(self.p_cathode)
        self.cathode.set_power(self.p_cathode)

    def set_rrg(self):
        self.gas = round(self.ui_main.set_rrg.value(), 2)
        self.ui_main.set_rrg_slider.setValue(self.gas)
        self.rrg.set_flow(self.gas)

    def set_rrg_slider(self):
        self.gas = round(self.ui_main.set_rrg_slider.value(), 2)
        self.ui_main.set_rrg.setValue(self.gas)
        self.rrg.set_flow(self.gas)

    def get_rrg_state(self):
        state = self.rrg.get_state()
        self.ui_main.set_rrg_state.setCurrentIndex(state)

    def set_rrg_state(self):
        # 0 - открыт, 1 - закрыт, 2 - регулировка
        state = self.ui_main.set_rrg_state.currentIndex()
        self.rrg.set_state(state)
        if state == 0:
            self.ui_main.set_rrg.setDisabled(False)
            self.ui_main.set_rrg_slider.setDisabled(False)
            # TODO: setValue to maximum displayable value
            self.ui_main.set_rrg.setValue(300)
            self.ui_main.set_rrg_slider.setValue(300)
        if state == 1:
            self.ui_main.set_rrg.setDisabled(True)
            self.ui_main.set_rrg_slider.setDisabled(True)
            self.ui_main.set_rrg.setValue(0)
            self.ui_main.set_rrg_slider.setValue(0)
            self.rrg.set_flow(0)
        if state == 2:
            self.ui_main.set_rrg.setDisabled(False)
            self.ui_main.set_rrg_slider.setDisabled(False)
    
    def set_gas(self):
        gas = self.ui_main.set_gas.currentText()
        self.pressure_1.set_gas(gas)
        self.pressure_2.set_gas(gas)
        self.pressure_3.set_gas(gas)
