# connect to a single dynamic reconfigure server
import os
import rospkg
import rospy
import threading
import time

from dynamic_reconfigure.client import Client
from functools import partial
from qt_gui.plugin import Plugin
from python_qt_binding import loadUi
# TODO(lucasw) need a library version detection to switch between these?
# ImportError: cannot import name QCheckBox
# from python_qt_binding.QtGui import QCheckBox, QGridLayout, QHBoxLayout,
# QLabel, QLineEdit, QVBoxLayout, QSlider, QWidget
# this works in qt5 kinetic
from python_qt_binding.QtCore import QTimer, Signal
from python_qt_binding.QtGui import QDoubleValidator, QIntValidator
from python_qt_binding.QtWidgets import QCheckBox, QComboBox, QGridLayout
from python_qt_binding.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QSlider, QWidget

from python_qt_binding import QtCore
from std_msgs.msg import Int32


class DrSingle(Plugin):
    do_update_description = QtCore.pyqtSignal(list)
    do_update_config = QtCore.pyqtSignal(dict)
    do_update_checkbox = QtCore.pyqtSignal(bool)
    do_update_dr = QtCore.pyqtSignal()

    def __init__(self, context):
        super(DrSingle, self).__init__(context)
        # Give QObjects reasonable names
        self.setObjectName('DrSingle')

        # Process standalone plugin command-line arguments
        from argparse import ArgumentParser
        parser = ArgumentParser()
        # Add argument(s) to the parser.
        parser.add_argument("-q", "--quiet", action="store_true",
                            dest="quiet",
                            help="Put plugin in silent mode")
        args, unknowns = parser.parse_known_args(context.argv())
        if not args.quiet:
            print('arguments: {}'.format(args))
            print('unknowns: {}'.format(unknowns))

        self.rospack = rospkg.RosPack()

        # Create QWidget
        self._widget = QWidget()
        # Get path to UI file which is a sibling of this file
        # in this example the .ui and .py file are in the same folder
        ui_file = os.path.join(self.rospack.get_path('rqt_dr_single'), 'resource', 'dr_single.ui')
        # Extend the widget with all attributes and children from UI file
        loadUi(ui_file, self._widget)
        # Give QObjects reasonable names
        self._widget.setObjectName('DrSingleUi')
        # Show _widget.windowTitle on left-top of each plugin (when
        # it's set in _widget). This is useful when you open multiple
        # plugins at once. Also if you open multiple instances of your
        # plugin at once, these lines add number to make it easy to
        # tell from pane to pane.
        if context.serial_number() > 1:
            self._widget.setWindowTitle(self._widget.windowTitle() + (' (%d)' % context.serial_number()))
        # Add widget to the user interface
        context.add_widget(self._widget)
        # self.parent_layout = self._widget.findChild(QVBoxLayout, 'vertical_layout')
        self.layout = self._widget.findChild(QGridLayout, 'grid_layout')
        self.changed_value = {}

        self.lock = threading.Lock()

        self.reset()
        self.do_update_description.connect(self.update_description)
        self.do_update_config.connect(self.update_config)
        self.do_update_checkbox.connect(self.update_checkbox)
        self.do_update_dr.connect(self.update_dr)
        self.div = 100.0

        server_name = rospy.get_param("~server", None)
        if server_name is not None and server_name[0] != '/':
            server_name = rospy.get_namespace() + server_name
        text = ("server name is '" + str(server_name) + "'")
        if server_name is None:
            rospy.logwarn(text)
            # server_name = "test"
        else:
            rospy.loginfo(text)
        with self.lock:
            self.server_name = server_name

        self.hide_dropdown = rospy.get_param("~hide_dropdown", None)

        self.refresh_button = self._widget.findChild(QPushButton, 'refresh_button')
        self.refresh_button.pressed.connect(self.update_topic_list)

        self.connected_checkbox = self._widget.findChild(QCheckBox, 'connected_checkbox')
        self.connected_checkbox.setChecked(False)
        self.connected_checkbox.setEnabled(False)
        self.server_combobox = self._widget.findChild(QComboBox, 'server_combobox')
        self.server_combobox.currentIndexChanged.connect(self.server_changed)
        self.client = None
        self.update_topic_list()

        # try to connect to saved dr server
        self.connect_dr()
        # TODO(lucasw) can't use ros timers in guis, there might be a sim clock that
        # is paused.  (How many other nodes in other projects are going to fail
        # because of that?)
        # self.update_timer = rospy.Timer(rospy.Duration(0.05), self.update_dr_configuration)
        # TODO(lucasw) is this the right thread to be calling this?
        # If this is qt then need to trigger a ros callback-
        # can I do a single shot rospy.Timer with sim_clock paused?
        # self.do_update_dr.emit()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_dr_from_emit)
        self.timer.start(100)

    def update_dr_from_emit(self):
        self.do_update_dr.emit()

    def update_dr(self):
        self.update_dr_configuration(None)
        # TODO(lucasw) need to call this repeatedly, setup time callback here

    def update_topic_list(self):
        with self.lock:
            server_name = self.server_name
        # TODO(lucasw) if the roscore goes down, this throws a socket error
        topics = rospy.get_published_topics()
        self.server_combobox.currentIndexChanged.disconnect(self.server_changed)
        self.server_combobox.clear()
        rospy.logdebug(server_name)
        if server_name is not None:
            self.server_combobox.addItem(server_name)
        dr_list = []
        for topic in topics:
            if topic[1] == 'dynamic_reconfigure/ConfigDescription':
                new_server_name = topic[0][:topic[0].rfind('/')]
                if new_server_name != server_name:
                    dr_list.append(new_server_name)
                # default to choosing the first one
                # if server_name is None:
                #     with self.lock:
                #         self.server_name = server_name
        dr_list.sort()
        self.server_combobox.addItems(dr_list)
        self.server_combobox.currentIndexChanged.connect(self.server_changed)

        # force the gui to be refreshed
        # self.client = None
        if self.client is None:
            self.connect_dr()
        self.update_dr_configuration(None)

    def server_changed(self, index):
        new_server = self.server_combobox.currentText()
        rospy.loginfo(new_server)
        if self.server_name != new_server:
            self.server_name = new_server
            self.client = None
            self.changed_value = {}
            self.reset()
            self.refresh_button.click()

    def description_callback(self, description):
        # self.description = description
        self.do_update_description.emit(description)

    def reset(self, use_lock=True):
        # TODO(lucasw) actually use the lock optionally
        with self.lock:
            rospy.logdebug("reset")
            self.described = False
            self.widget = {}
            self.enum_values = {}
            self.enum_inds = {}
            self.connections = {}
            self.use_div = {}
            self.params = {}
            self.val_label = {}
            self.config = None

    def add_label(self, name, row):
        # TODO(lucasw) don't really need this
        return
        val_label = QLabel()
        val_label.setFixedWidth(90)
        self.layout.addWidget(val_label, row, 2)
        self.val_label[name] = val_label

    def make_line_edit(self, name, row, vmin, vmax, double_not_int):
        val_edit = QLineEdit()
        val_edit.setFixedWidth(100)
        # TODO(lucasw) have optional ability to break limits
        if double_not_int:
            val_edit.setValidator(QDoubleValidator(vmin,
                                                   vmax, 8, self))
        else:
            val_edit.setValidator(QIntValidator(vmin,
                                                vmax, self))
        connection_name = name + "_line_edit"
        self.connections[connection_name] = partial(self.text_changed,
                                                    name)
        val_edit.editingFinished.connect(self.connections[connection_name])
        self.layout.addWidget(val_edit, row, 2)
        self.val_label[name] = val_edit
        return val_edit

    # Setup the gui according to the dr description
    # TODO(lucasw) but it doesn't have all the information, can get that out of
    # the config callback and this combined?
    def update_description(self, description):
        if rospy.is_shutdown():
            return
        with self.lock:
            updated = self.update_description_inner(description)
        if updated:
            rospy.loginfo("updated description")
            if self.config:
                self.update_config(self.config)

    #  -> bool:
    def update_description_inner(self, description):
        # clear the layout
        # rospy.loginfo("clearing layout " + str(self.layout.count()))
        try:
            for i in reversed(range(self.layout.count())):
                layout = self.layout.itemAt(i).layout()
                if layout:
                    for j in reversed(range(layout.count())):
                        layout.itemAt(j).widget().setParent(None)
                    self.layout.itemAt(i).layout().setParent(None)
                elif self.layout.itemAt(i).widget():
                    self.layout.itemAt(i).widget().setParent(None)
        except RuntimeError as ex:
            rospy.logerr(ex)
            return False
        # TODO(lucasw) this has the min and max values and types from which to
        # generate the gui
        # But no group information
        # rospy.loginfo(description)
        row = 0
        for param in description:
            self.params[param['name']] = param
            rospy.logdebug(param['name'] + " " + str(param['min']) + " "
                           + str(param['max']) + " " + str(param['type']))

            label = QLabel()
            label.setText(param['name'])
            self.layout.addWidget(label, row, 0)

            widget = None
            if param['type'] == 'str':
                widget = QLineEdit()
                widget.setText(param['default'])
                widget.editingFinished.connect(partial(self.text_resend, param['name']))
                self.add_label(param['name'], row)
                self.layout.addWidget(widget, row, 1)
            elif param['type'] == 'bool':
                widget = QCheckBox()
                widget.setChecked(param['default'])
                self.layout.addWidget(widget, row, 1)
                self.use_div[param['name']] = False
                self.connections[param['name']] = partial(self.value_changed, param['name'])
                widget.toggled.connect(self.connections[param['name']])
                self.add_label(param['name'], row)
                self.layout.addWidget(widget, row, 1)
            elif param['type'] == 'double':
                # TODO(lucasw) also have qspinbox or qdoublespinbox
                layout = QHBoxLayout()
                widget = QSlider()
                slider_val = 0.0
                if param['min'] != param['max']:
                    slider_val = self.div * (param['default'] - param['min']) / (param['max'] - param['min'])
                widget.setValue(int(slider_val))
                widget.setOrientation(QtCore.Qt.Horizontal)
                widget.setMinimum(0)
                widget.setMaximum(int(self.div))
                self.use_div[param['name']] = True
                self.connections[param['name']] = partial(self.value_changed,
                                                          param['name'])
                widget.valueChanged.connect(self.connections[param['name']])
                layout.addWidget(widget)

                line_edit = self.make_line_edit(param['name'], row,
                                                param['min'], param['max'],
                                                double_not_int=True)
                layout.addWidget(line_edit)
                self.layout.addLayout(layout, row, 1)

            elif param['type'] == 'int':
                # TODO(lucasw) also have qspinbox or qdoublespinbox
                if param['edit_method'] == '':
                    layout = QHBoxLayout()
                    widget = QSlider()
                    widget.setValue(param['default'])
                    widget.setOrientation(QtCore.Qt.Horizontal)
                    widget.setMinimum((param['min']))
                    widget.setMaximum((param['max']))
                    self.connections[param['name']] = partial(self.value_changed, param['name'])
                    widget.valueChanged.connect(self.connections[param['name']])
                    layout.addWidget(widget)
                    line_edit = self.make_line_edit(param['name'], row,
                                                    param['min'], param['max'],
                                                    double_not_int=False)
                    layout.addWidget(line_edit)
                    self.layout.addLayout(layout, row, 1)
                else:  # enum
                    widget = QComboBox()
                    # edit_method is actually a long string that has to be interpretted
                    # back into a list
                    enums = eval(param['edit_method'])['enum']
                    self.enum_values[param['name']] = {}
                    self.enum_inds[param['name']] = {}
                    count = 0
                    for enum in enums:
                        name = enum['name'] + ' (' + str(enum['value']) + ')'
                        widget.addItem(name)
                        self.enum_values[param['name']][count] = enum['value']
                        self.enum_inds[param['name']][enum['value']] = count
                        count += 1
                        # print(count, enum)
                    self.connections[param['name']] = partial(self.enum_changed,
                                                              param['name'])
                    widget.currentIndexChanged.connect(self.connections[param['name']])
                    self.add_label(param['name'], row)
                    self.layout.addWidget(widget, row, 1)
                self.use_div[param['name']] = False
            else:
                rospy.logerr(param)

            if widget:
                self.widget[param['name']] = widget
                # val_label.setText("0")
                # self.layout.addWidget(val_label, row, 2)
                row += 1
        self.described = True
        return True

    def config_callback(self, config):
        # The first config/description callback happen out of order-
        # the description is updated after the config, so need to store it.
        self.do_update_config.emit(config)

    def update_config(self, config):
        if not config:
            return
        if not self.described:
            self.config = config
            return
        # if not self.client:
        #     return
        rospy.logdebug(config)
        with self.lock:
            self.update_config_inner(config)

    def update_config_inner(self, config):
        for param_name in config.keys():
            if param_name not in self.widget.keys():
                continue
            try:
                if param_name in self.val_label.keys() and param_name in self.params.keys():
                    if self.params[param_name]['type'] == 'int':
                        text = str(config[param_name])
                    else:
                        val = config[param_name]
                        max_dec = 11
                        # text = str(val)
                        num_before_decimal = len(str(int(val)))
                        num_after_decimal = max(max_dec - num_before_decimal - 1, 1)
                        text = "{:0.{prec}f}".format(config[param_name], prec=num_after_decimal)
                        if True:
                            text = text.rstrip("0")
                            if text[-1] == '.':
                                text += "0"
                        if len(text) > max_dec:
                            text = "{:g}".format(config[param_name])
                        # print(param_name, num_before_decimal, num_after_decimal, val, text, len(text))
                    self.val_label[param_name].setText(text)
                # TODO(lucasw) also need to change slider
                value = config[param_name]
                if isinstance(self.widget[param_name], QSlider):
                    try:
                        self.widget[param_name].valueChanged.disconnect()
                    except TypeError as e:
                        # TOOD(lucasw) not sure in what circumstances this fails
                        rospy.logwarn(param_name + " disconnect failed " + str(e))
                        # TODO(lucasw) if that failed will the connect work?
                    if self.use_div[param_name]:
                        min_val = self.params[param_name]['min']
                        max_val = self.params[param_name]['max']
                        if min_val != max_val:
                            old_val = value
                            value = (self.div * (value - min_val) / (max_val - min_val))
                            # if self.use_div[param_name]:
                            #     print('update config', param_name, old_val, value, min_val, max_val)
                        else:
                            value = self.div
                    try:
                        self.widget[param_name].setValue(int(value))
                        self.widget[param_name].valueChanged.connect(self.connections[param_name])
                    except TypeError as ex:
                        rospy.logerr("{} {} {}".format(param_name, value, ex))
                elif isinstance(self.widget[param_name], QLineEdit):
                    self.widget[param_name].setText(value)
                elif isinstance(self.widget[param_name], QCheckBox):
                    self.widget[param_name].setChecked(value)
                elif isinstance(self.widget[param_name], QComboBox):
                    try:
                        self.widget[param_name].setCurrentIndex(self.enum_inds[param_name][value])
                    except KeyError as ex:
                        rospy.logerr("{} {} {}".format(param_name, value, ex))
            except RuntimeError as ex:
                pass
                # rospy.logerr(param_name + str(ex))
                # self.reset(use_lock=False)
                # break

    def text_resend(self, name):
        self.changed_value[name] = self.widget[name].text()
        # TODO(lucasw) wanted to avoid these with a timered loop, but doing it direct for now
        # self.do_update_dr.emit()

    def enum_changed(self, name, ind):
        if ind not in self.enum_values[name].keys():
            return
        #     rospy.logerr(name + " values ind mismatch " + str(ind) + " " +
        #                  str(self.enum_values[name].keys()))
        #     return
        self.changed_value[name] = self.enum_values[name][ind]
        # TODO(lucasw) wanted to avoid these with a timered loop, but doing it direct for now
        # self.do_update_dr.emit()

    def text_changed(self, name):
        value = float(self.val_label[name].text())
        self.changed_value[name] = value
        # TODO(lucasw) wanted to avoid these with a timered loop, but doing it direct for now
        # self.do_update_dr.emit()

    def value_changed(self, name, value, min_val=None, max_val=None):
        if self.use_div[name]:
            min_val = self.params[name]['min']
            max_val = self.params[name]['max']
            old_val = value
            value = min_val + (max_val - min_val) * value / self.div
            # print('val changed', name, old_val, value, min_val, max_val, self.div)
        self.changed_value[name] = value
        # TODO(lucasw) wanted to avoid these with a timered loop, but doing it direct for now
        # self.do_update_dr.emit()

    def update_checkbox(self, value):
        self.connected_checkbox.setChecked(value)

    def connect_dr(self):
        with self.lock:
            server_name = self.server_name
        if server_name is None:
            return
        try:
            # TODO(lucasw) surely this timeout has nothing to do with ros time
            # and instead uses wall time.
            # This takes up 0.3 second no matter what
            # and seems to block the main gui thread  though I haven't
            # exhausted options for running it in other threads
            self.client = Client(server_name, timeout=0.2,
                                 config_callback=self.config_callback,
                                 description_callback=self.description_callback)
            self.do_update_checkbox.emit(True)
        except Exception as ex:  # ROSException:
            rospy.logdebug("no server " + str(server_name))

    def update_dr_configuration(self, evt):
        if self.client is None:
            return
        if len(self.changed_value.keys()) > 0:
            update_timeout = 2.0
            # TODO(lucasw) could follow
            # https://stackoverflow.com/questions/2829329/catch-a-threads-exception-in-the-caller-thread-in-python
            # and pass a message back if the update configuration fails
            t0 = rospy.Time.now()
            try:
                th1 = threading.Thread(target=self.client.update_configuration,
                                       args=[self.changed_value])
                th1.start()
                t1 = rospy.Time.now()
                while ((rospy.Time.now() - t1).to_sec() < update_timeout):
                    if th1.is_alive():
                        rospy.sleep(0.02)
                    else:
                        break
                if th1.is_alive():
                    # TODO(lucasw) how to kill t1- or does it matter?
                    raise RuntimeError("timeout")
            except Exception as ex:
                elapsed = (rospy.Time.now() - t0).to_sec()
                text = ("lost connection to server"
                        + str(self.server_name)
                        + " after elapsed: "
                        + str(s))
                rospy.logerr(text)
                rospy.logerr(ex)
                self.client = None
                self.do_update_checkbox.emit(False)
                return
            self.changed_value = {}

    def shutdown_plugin(self):
        # self.reset()
        # TODO unregister all publishers here
        self.timer.stop()

    def save_settings(self, plugin_settings, instance_settings):
        rospy.logdebug("saving server " + self.server_name)
        instance_settings.set_value('server_name', self.server_name)
        instance_settings.set_value('hide_dropdown', self.hide_dropdown)
        # goes to ~/.config/ros.org/rqt_gui.ini, or into .perspective

    # This is called after init
    def restore_settings(self, plugin_settings, instance_settings):
        if instance_settings.contains('server_name') and self.server_name is None:
            with self.lock:
                self.server_name = instance_settings.value('server_name')
            rospy.logdebug("restore server " + self.server_name)
            self.update_topic_list()
        if self.server_name is None:
            self.server_changed(0)

        if instance_settings.contains('hide_dropdown') and self.hide_dropdown is None:
            # instance settings don't resolve as True of False boolean
            # rospy.loginfo(type(instance_settings.value('hide_dropdown'))) # 'unicode'
            self.hide_dropdown = instance_settings.value('hide_dropdown') == 'true'
        if self.hide_dropdown is None:
            self.hide_dropdown = False
        if self.hide_dropdown:
            self.server_combobox.hide()

    # def trigger_configuration(self):
        # Comment in to signal that the plugin has a way to configure
        # This will enable a setting button (gear icon) in each dock widget title bar
        # Usually used to open a modal configuration dialog
