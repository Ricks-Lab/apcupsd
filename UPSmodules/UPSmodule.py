#!/usr/bin/env python3
"""UPSmodule  -  utility for interacting with compatible UPSs

    Copyright (C) 2019  RicksLab

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""
from __future__ import annotations
__author__ = "RicksLab"
__copyright__ = "Copyright (C) 2019 RicksLab"
__credits__ = ['Natalya Langford - Configuration Parser']
__license__ = "GNU General Public License"
__program_name__ = "ups-utils"
__maintainer__ = "RicksLab"
__docformat__ = 'reStructuredText'
# pylint: disable=multiple-statements
# pylint: disable=line-too-long
# pylint: disable=bad-continuation

import os
import sys
import re
import shlex
import shutil
import time
import datetime
import json
import subprocess
import logging
import configparser
from enum import Enum
from typing import Tuple, List, Union, Dict, Generator, Set
from uuid import uuid4
from UPSmodules import env


LOGGER = logging.getLogger('ups-utils')


class UpsEnum(Enum):
    """
    Replace __str__ method of Enum so that name excludes type and can be used as key in other dicts.
    """
    def __str__(self) -> str:
        return self.name

class ObjDict(dict):
    """
    Allow access of dictionary keys by key name.
    """
    # pylint: disable=attribute-defined-outside-init
    # pylint: disable=too-many-instance-attributes
    def __getattr__(self, name) -> str:
        if name in self:
            return self[name]
        raise AttributeError('No such attribute: {}'.format(name))

    def __setattr__(self, name, value) -> None:
        self[name] = value

    def __delattr__(self, name) -> None:
        if name in self:
            del self[name]
        else:
            raise AttributeError('No such attribute: {}'.format(name))

class UpsItem:
    _json_keys: Set[str, ...] = {'ups_IP', 'display_name', 'ups_type', 'daemon', 'snmp_community'}

    def __init__(self, json_details: dict):
        # UPS list from ups-config.json for monitor and ls utils.
        self.ups_list = {}
        self.prm: Dict[str, Union[str, bool, UpsComm.MIB_nmc, dict, None]] = {
            'uuid': uuid4().hex,
            'ups_IP': None,
            'display_name': None,
            'ups_type': None,
            'mib_commands': None,
            'snmp_community': None,
            'daemon': False,
            'valid_ip': False,
            'compatible': False,
            'accessible': False,
            'responsive': False}

        # Load initial data from json dict.
        for item_name, item_value in json_details.items():
            if item_name not in self._json_keys:
                LOGGER.debug('%s: Invalid key [%s] ignored', env.UT_CONST.ups_json_file, item_name)
                continue
            self.prm[item_name] = item_value

        # Check accessibility
        self.ups_comm = UpsComm(self)
        if self.prm['ups_type'] in self.ups_comm.MIB_nmc.__members__:
            self.prm['compatible'] = True
            self.prm['ups_type'] = self.ups_comm.MIB_nmc(self.prm['ups_type'])
        if self.ups_comm.is_valid_ip_fqdn(self.prm['upsIP']):
            self.prm['valid_ip'] = True
        if self.ups_comm.check_ip_access(self.prm['upsIP']):
            self.prm['accessible'] = True
        if self.ups_comm.check_snmp_response(self):
            self.prm['responsive'] = True

        self.prm['mib_commands'] = self.ups_comm.all_mib_cmds[self.prm['ups_type']]

    def __str__(self):
        str_rep = ''
        for name, value in self.prm.items():
            if str_rep:
                str_rep = '{}\n{}:\n'.format(str_rep, name)
            else:
                str_rep = '{}:\n'.format(name)
            for ups_param_name, ups_param_value in value.items():
                str_rep = '{}\n    {}: {}\n'.format(str_rep, ups_param_name, ups_param_value)
        return str_rep

    def get_ups_parameter_value(self, param_name: str) -> Union[str, None]:
        """ Get ups parameter value for parameter name from target UPS or active UPS if not specified

        :param param_name: Target parameter name
        :return: Parameter value as string else None
        """
        return self.prm[param_name] if param_name in self.prm.keys() else None

    def ups_uuid(self) -> str:
        """ Get the uuid value for the target UPS or active UPS if target is None.

        :return:  The uuid as a int.
        """
        return self.prm['uuid']

    def ups_name(self) -> str:
        """ Get the name value for the target UPS or active UPS if target is None.

        :return:  The name as a str.
        """
        return self.prm['display_name']

    def ups_type(self) -> str:
        """ Get the type value for the target UPS or active UPS if target is None.

        :return:  The ups_type as a str.
        """
        return self.prm['ups_type'].name

    def ups_nmc_model(self) -> str:
        """ Get the type value for the target UPS or active UPS if target is None.

        :return:  The ups_type as a str.
        """
        return self.prm['ups_nmc_model']

    def ups_ip(self) -> None:
        """ Get the IP address value for the target UPS or active UPS if target is None.

        :return:  The IP address as a str.
        """
        return self.prm['ups_IP']

    # Set parameters required for daemon mode.
    def set_daemon_parameters(self) -> bool:
        """ Set all daemon parameters based on defaults in env.UT_CONST and the config.py file.

        :return:  True on success
        """
        read_status = True
        if not env.UT_CONST.ups_config_ini:
            print('Error ups-utils.ini filename not set.')
            return False
        config = configparser.ConfigParser()
        try:
            config.read(env.UT_CONST.ups_config_ini)
        except configparser.Error as err:
            LOGGER.exception('config parser error: %s', err)
            print('Error in ups-utils.ini file.  Using defaults')
            return True
        LOGGER.debug('config[DaemonPaths]: %s', dict(config['DaemonPaths']))
        LOGGER.debug('config[DaemonScripts]: %s', dict(config['DaemonScripts']))
        LOGGER.debug('config[DaemonParameters]: %s', dict(config['DaemonParameters']))

        # Set path definitions
        for path_name in self.daemon_paths:
            if isinstance(config['DaemonPaths'][path_name], str):
                self.daemon_params[path_name] = os.path.expanduser(config['DaemonPaths'][path_name])
                if self.daemon_params[path_name]:
                    if not os.path.isdir(self.daemon_params[path_name]):
                        if path_name == 'boinc_home':
                            print('BOINC_HOME directory [{}] not found. Set to None'.format(
                                self.daemon_params[path_name]))
                        else:
                            print('Missing directory for {} path_name: {}'.format(
                                path_name, self.daemon_params[path_name]))
                            read_status = False
        if self.daemon_params['boinc_home']:
            os.environ['BOINC_HOME'] = self.daemon_params['boinc_home']

        # Set script definitions
        for script_name in self.daemon_scripts:
            if isinstance(config['DaemonScripts'][script_name], str):
                self.daemon_params[script_name] = os.path.join(self.daemon_params['ups_utils_script_path'],
                                                               config['DaemonScripts'][script_name])
                if self.daemon_params[script_name]:
                    if not os.path.isfile(self.daemon_params[script_name]):
                        print('Missing {} script: {}'.format(script_name, self.daemon_params[script_name]))
                        read_status = False

        # Set script parameters
        for parameter_name in self.daemon_param_names:
            if re.search(env.UT_CONST.PATTERNS['INI'], config['DaemonParameters'][parameter_name]):
                raw_param = re.sub(r'\s+', '', config['DaemonParameters'][parameter_name])
                params = tuple(int(x) for x in raw_param[1:-1].split(','))
                if parameter_name == 'read_interval':
                    self.daemon_params[parameter_name]['monitor'] = params[0]
                    self.daemon_params[parameter_name]['daemon'] = params[1]
                else:
                    self.daemon_params[parameter_name]['crit'] = params[0]
                    self.daemon_params[parameter_name]['warn'] = params[1]
            else:
                LOGGER.debug('Incorrect format for %s parameter: %s',
                             parameter_name, config['DaemonParameters'][parameter_name])
                print('Incorrect format for {} parameter: {}'.format(
                    parameter_name, config['DaemonParameters'][parameter_name]))
                print('Using default value: {}'.format(self.daemon_params[parameter_name]))

        # Check Daemon Parameter Values
        for parameter_name in self.daemon_param_names:
            if parameter_name == 'read_interval':
                for sub_parameter_name in {'monitor', 'daemon'}:
                    if self.daemon_params[parameter_name][sub_parameter_name] < \
                            self.daemon_params[parameter_name]['limit']:
                        env.UT_CONST.process_message('Warning invalid {}-{} value [{}], using defaults'.format(
                                               parameter_name, sub_parameter_name,
                                               self.daemon_params[parameter_name][sub_parameter_name]), verbose=True)
                        self.daemon_params[parameter_name] = self.daemon_param_defaults[parameter_name].copy()
            else:
                reset = False
                if self.daemon_param_defaults[parameter_name]['crit'] > \
                        self.daemon_param_defaults[parameter_name]['warn']:
                    if self.daemon_params[parameter_name]['crit'] <= self.daemon_params[parameter_name]['warn']:
                        reset = True
                        env.UT_CONST.process_message('Warning crit must be > warn value, '
                                                     'using defaults for {}'.format(parameter_name), verbose=True)
                    if self.daemon_params[parameter_name]['crit'] < self.daemon_params[parameter_name]['limit']:
                        reset = True
                        env.UT_CONST.process_message('Warning crit must be >= limit value, '
                                                     'using defaults for {}'.format(parameter_name), verbose=True)
                    if self.daemon_params[parameter_name]['warn'] < self.daemon_params[parameter_name]['limit']:
                        reset = True
                        env.UT_CONST.process_message('Warning warn must be >= limit value, '
                                                     'using defaults for {}'.format(parameter_name), verbose=True)
                else:
                    if self.daemon_params[parameter_name]['crit'] >= self.daemon_params[parameter_name]['warn']:
                        reset = True
                        env.UT_CONST.process_message('Warning crit must be < warn value, '
                                                     'using defaults for {}'.format(parameter_name), verbose=True)
                    if self.daemon_params[parameter_name]['crit'] < self.daemon_params[parameter_name]['limit']:
                        reset = True
                        env.UT_CONST.process_message('Warning crit must be >= limit value, '
                                                     'using defaults for {}'.format(parameter_name), verbose=True)
                    if self.daemon_params[parameter_name]['warn'] < self.daemon_params[parameter_name]['limit']:
                        reset = True
                        env.UT_CONST.process_message('Warning warn must be >= limit value, '
                                                     'using defaults for {}'.format(parameter_name), verbose=True)
                if reset:
                    self.daemon_params[parameter_name] = self.daemon_param_defaults[parameter_name].copy()
        return read_status

    def print_daemon_parameters(self) -> None:
        """ Print all daemon parameters.

        :return:  None
        """
        print('Daemon parameters:')
        for param_name, param_value in self.daemon_params.items():
            print('    {}: {}'.format(param_name, param_value))

    def execute_script(self, script_name: str) -> bool:
        """ Execute script defined in the daemon parameters

        :param: script_name: name of script to be executed
        :return:  True on success
        """
        if script_name not in self.daemon_scripts:
            raise AttributeError('Error: {} no valid script name: [{}]'.format(script_name, self.daemon_scripts))
        if not self.daemon_params[script_name]:
            print('No {} defined'.format(script_name))
            return False
        try:
            cmd = subprocess.Popen(shlex.split(self.daemon_params[script_name]),
                                   shell=False, stdout=subprocess.PIPE)
            while True:
                if cmd.poll() is not None:
                    break
                time.sleep(0.2)
            if cmd.returncode:
                env.UT_CONST.process_message('{} failed with return code: [{}]'.format(script_name, cmd.returncode),
                                             verbose=True)
                return False
        except subprocess.CalledProcessError as err:
            print('Error [{}]: could not execute script: {}'.format(err,
                  self.daemon_params[script_name]), file=sys.stderr)
            return False
        return True


class UpsList:
    def __init__(self):
        self.list: Dict[str, UpsItem] = {}

    def __repr__(self) -> str:
        return str(self.list)

    def __str__(self) -> str:
        num_ups = self.get_num_ups_tuple()
        out_str = '{} UPSs listed in {}.'.format(num_ups[0], env.UT_CONST.ups_json_file)
        print(
            '    {} are compatible, {} are accessible, {} are responsive\n'.format(num_ups[1], num_ups[2], num_ups[3]))
        return 'UPS_List: Number of UPSs: {}'.format(self.num_upss())

    def __getitem__(self, uuid: str) -> UpsItem:
        if uuid in self.list:
            return self.list[uuid]
        raise KeyError('KeyError: invalid uuid: {}'.format(uuid))

    def __setitem__(self, uuid: str, value: UpsItem) -> None:
        self.list[uuid] = value

    def __iter__(self) -> Generator[UpsItem, None, None]:
        for value in self.list.values():
            yield value

    def items(self) -> Generator[Union[str, UpsItem], None, None]:
        """
        Get uuid, gpu pairs from a GpuList object.

        :return:  uuid, gpu pair
        """
        for key, value in self.list.items():
            yield key, value

    def uuids(self) -> Generator[str, None, None]:
        """
        Get uuids of the GpuList object.

        :return: uuids from the GpuList object.
        """
        for key in self.list:
            yield key

    def gpus(self) -> Generator[UpsItem, None, None]:
        """
        Get UpsItems from a GpuList object.

        :return: GpuUItem
        """
        return self.__iter__()

    def add(self, ups_item: UpsItem) -> None:
        """
        Add given UpsItem to the UpsList.

        :param ups_item:  Item to be added
        """
        self[ups_item.prm.uuid] = ups_item
        LOGGER.debug('Added UPS Item %s to UPS List', ups_item.prm.uuid)

    def num_upss(self, vendor: Enum = UpsItem.UpsType.ALL) -> Dict[str, int]:
        """

        :param vendor:
        :return:
        """
        pass

    def read_all_ups_list_items(self, command_list: list, errups: bool = True, display: bool = False) -> dict:
        """ Get the specified list of monitor mib commands for all UPSs.

        :param command_list:  A list of mib commands to be read from the all UPSs.
        :param errups: Flag to indicate if error UPS should be included.
        :param display: Flag to indicate if parameters should be displayed as read.
        :return:  dict of results from the reading of all commands from all UPSs.
        """
        results = {}
        for ups_name, ups_item in self.get_ups_list().items():
            if not errups:
                if not self.is_responsive(ups_item):
                    continue
            self.set_active_ups(ups_item)
            results[ups_name] = self.read_ups_list_items(command_list, ups display=display)
            return results

    def read_ups_json(self) -> bool:
        """Reads the ups-config.json file which contains parameters for UPSs to be used by utility.

        :return: boolean True if no problems reading list
        """
        if not env.UT_CONST.ups_json_file or not os.path.isfile(env.UT_CONST.ups_json_file):
            print('Error: {} file not found: {}'.format(os.path.basename(env.UT_CONST.ups_json_file),
                                                        env.UT_CONST.ups_json_file))
            return False
        try:
            with open(env.UT_CONST.ups_json_file, 'r') as ups_list_file:
                ups_item = json.load(ups_list_file)
            for ups, ups_item in self.ups_list.items():
                ups_item['ups_nmc_model'] = ups_item['ups_type']
        except FileNotFoundError as error:
            env.UT_CONST.process_message("Error: file not found error for [{}]: {}".format(env.UT_CONST.ups_json_file,
                                                                                           error), verbose=True)
            return False
        except PermissionError as error:
            env.UT_CONST.ups_json_file("Error: permission error for [{}]: {}".format(env.UT_CONST.ups_json_file,
                                                                                     error), verbose=True)
            return False
        self.daemon_params['ups_json_file'] = env.UT_CONST.ups_json_file
        return True

    # Methods to get, check, and list UPSs
    def get_name_for_ups_uuid(self, ups_uuid: int) -> str:
        """ Get the ups name for a given uuid

        :param ups_uuid: Universally unique identifier for a UPS
        :return: name of the ups
        """
        for name, ups in self.ups_list.items():
            if ups['uuid'] == ups_uuid:
                return str(name)
        return 'Error'

    def get_uuid_for_ups_name(self, ups_name: str) -> str:
        """ Get uuid for ups with given name.

        :param ups_name: The target ups name.
        :return: The uuid as str or 'Error' if not found
        """
        for ups in self.ups_list.values():
            if ups['display_name'] == ups_name:
                return ups['uuid']
        return 'Error'

    def get_ups_list(self, errups: bool = True) -> dict:
        """Get the dictionary list of UPSs read at start up.

        :param errups: Flag to indicate if UPSs with errors should be included
        :return:  dictionary representing the list of UPSs
        """
        return_list = {}
        for ups_name, ups_item in self.ups_list.items():
            if not errups:
                if not self.is_responsive(ups_item):
                    continue
            return_list[ups_name] = ups_item
        return return_list

    def get_num_ups_tuple(self) -> Tuple[int]:
        """ This function will return a tuple of the UPS counts.

        :return: tuple represents listed, compatible, accessible, responsive UPSs
        """
        cnt = [0, 0, 0, 0]
        for ups in self.ups_list.values():
            cnt[0] += 1
            if self.is_compatible(ups):
                cnt[1] += 1
            if self.is_accessible(ups):
                cnt[2] += 1
            if self.is_responsive(ups):
                cnt[3] += 1
        return tuple(cnt)




class UpsComm:
    """ Class definition for UPS communication object."""

    # Configuration details
    daemon_paths: Tuple[str, ...] = ('boinc_home', 'ups_utils_script_path')
    daemon_scripts: Tuple[str, ...] = ('suspend_script', 'resume_script', 'shutdown_script', 'cancel_shutdown_script')
    daemon_param_names: Tuple[str, ...] = ('read_interval', 'threshold_battery_time_rem', 'threshold_time_on_battery',
                                           'threshold_battery_load', 'threshold_battery_capacity')
    daemon_param_defaults: Dict[str, Union[str, Dict[str, int]]] = {
        'ups_utils_script_path': os.path.expanduser('~/.local/bin/'),
        'read_interval': {'monitor': 10, 'daemon': 30, 'limit': 5},
        'threshold_battery_time_rem': {'crit': 5, 'warn': 10, 'limit': 4},
        'threshold_time_on_battery': {'crit': 5, 'warn': 3, 'limit': 1},
        'threshold_battery_load': {'crit': 90, 'warn': 80, 'limit': 10},
        'threshold_battery_capacity': {'crit': 10, 'warn': 50, 'limit': 5}}

    # Set params to defaults
    daemon_params: Dict[str, Union[str, dict]] = {
        'boinc_home': None, 'ups_utils_script_path': daemon_param_defaults['ups_utils_script_path'],
        'suspend_script': None, 'resume_script': None,
        'shutdown_script': None, 'cancel_shutdown_script': None,
        'read_interval': daemon_param_defaults['read_interval'].copy(),
        'threshold_battery_time_rem': daemon_param_defaults['threshold_battery_time_rem'].copy(),
        'threshold_time_on_battery': daemon_param_defaults['threshold_time_on_battery'].copy(),
        'threshold_battery_load': daemon_param_defaults['threshold_battery_load'].copy(),
        'threshold_battery_capacity': daemon_param_defaults['threshold_battery_capacity'].copy()}

    Text_style = Enum('style', 'warn crit green bold normal')
    # UPS response bit string decoders
    decoders: Dict[str, Tuple[str, ...]] = {
        'apc_system_status': ('Abnormal', 'OnBattery', 'LowBattery', 'OnLine', 'ReplaceBattery',
                              'SCE', 'AVR_Boost', 'AVR_Trim', 'OverLoad', 'RT_Calibration',
                              'BatteriesDischarged', 'ManualBypass', 'SoftwareBypass', 'Bypass-InternalFault',
                              'Bypass-SupplyFailure', 'Bypass-FanFailure', 'SleepOnTimer', 'SleepNoPower',
                              'On', 'Rebooting', 'BatterCommLost', 'ShutdownInitiated', 'Boost/TrimFailure',
                              'BadOutVoltage', 'BatteryChargerFail', 'HiBatTemp', 'WarnBatTemp', 'CritBatTemp',
                              'SelfTestInProgress', 'LowBat/OnBat', 'ShutdownFromUpstream',
                              'ShutdownFromDownstream', 'NoBatteriesAttached', 'SyncCmdsInProg',
                              'SyncSleepInProg', 'SyncRebootInProg', 'InvDCimbalance', 'TransferReadyFailure',
                              'Shutdown/Unable to Transfer', 'LowBatShutdown', 'FanFail', 'MainRelayFail',
                              'BypassRelayFail', 'TempBypass', 'HighInternalTemp', 'BatTempSensorFault',
                              'InputOORforBypass', 'DCbusOverV', 'PFCfailure', 'CritHWfail', 'Green/ECO mode',
                              'HotStandby', 'EPO', 'LoadAlarmViolation', 'BypassPhaseFault',
                              'UPSinternalComFail', 'EffBoosterMode', 'Off', 'Standby', 'Minor/EnvAlarm')}

    # UPS MiB Commands
    MIB_group = Enum('group', 'all static dynamic')
    monitor_mib_cmds: Dict[MIB_group, Tuple[str, ...]] = {
        MIB_group.static: ('mib_ups_name', 'mib_ups_info', 'mib_bios_serial_number', 'mib_firmware_revision',
                           'mib_ups_type', 'mib_ups_location', 'mib_ups_uptime'),
        MIB_group.dynamic: ('mib_ups_env_temp', 'mib_battery_capacity', 'mib_time_on_battery',
                            'mib_battery_runtime_remain', 'mib_input_voltage', 'mib_input_frequency',
                            'mib_output_voltage', 'mib_output_frequency', 'mib_output_load', 'mib_output_current',
                            'mib_output_power', 'mib_system_status', 'mib_battery_status')}
    output_mib_cmds: Tuple[str, ...] = ('mib_output_voltage', 'mib_output_frequency', 'mib_output_load',
                                        'mib_output_current', 'mib_output_power')
    input_mib_cmds: Tuple[str, ...] = ('mib_input_voltage', 'mib_input_frequency')

    MIB_nmc = Enum('nmc', 'apc_ap96xx eaton_pw')
    all_mib_cmds: Dict[MIB_nmc, Dict[str, Dict[str, Union[str, Dict[str, str], None]]]] = {
        # MiBs for APC UPS with AP96xx NMC
        MIB_nmc.apc_ap96xx: {
            'mib_ups_info': {'iso': 'iso.3.6.1.2.1.1.1.0',
                             'name': 'General UPS Information',
                             'decode': None},
            'mib_bios_serial_number': {'iso': 'iso.3.6.1.4.1.318.1.1.1.1.2.3.0',
                                       'name': 'UPS BIOS Serial Number',
                                       'decode': None},
            'mib_firmware_revision': {'iso': 'iso.3.6.1.4.1.318.1.1.1.1.2.1.0',
                                      'name': 'UPS Firmware Revision',
                                      'decode': None},
            'mib_ups_type': {'iso': 'iso.3.6.1.4.1.318.1.1.1.1.1.1.0',
                             'name': 'UPS Model Type',
                             'decode': None},
            'mib_ups_model': {'iso': 'iso.3.6.1.4.1.318.1.1.1.1.2.5.0',
                              'name': 'UPS Model Number',
                              'decode': None},
            'mib_ups_contact': {'iso': 'iso.3.6.1.2.1.1.4.0',
                                'name': 'UPS Contact',
                                'decode': None},
            'mib_ups_env_temp': {'iso': 'iso.3.6.1.4.1.318.1.1.25.1.2.1.6.1.1',
                                 'name': 'UPS Environment Temp',
                                 'decode': None},
            'mib_ups_location': {'iso': 'iso.3.6.1.2.1.1.6.0',
                                 'name': 'UPS Location',
                                 'decode': None},
            'mib_ups_uptime': {'iso': 'iso.3.6.1.2.1.1.3.0',
                               'name': 'UPS Up Time',
                               'decode': None},
            'mib_ups_manufacture_date': {'iso': 'iso.3.6.1.4.1.318.1.1.1.1.2.2.0',
                                         'name': 'UPS Manufacture Date',
                                         'decode': None},
            'mib_ups_name': {'iso': 'iso.3.6.1.2.1.33.1.1.5.0',
                             'name': 'UPS Name',
                             'decode': None},
            'mib_battery_capacity': {'iso': 'iso.3.6.1.4.1.318.1.1.1.2.2.1.0',
                                     'name': 'Percentage of Total Capacity',
                                     'decode': None},
            'mib_battery_temperature': {'iso': 'iso.3.6.1.4.1.318.1.1.1.2.2.2.0',
                                        'name': 'Battery Temperature in C',
                                        'decode': None},
            'mib_system_status': {'iso': 'iso.3.6.1.4.1.318.1.1.1.11.1.1.0',
                                  'name': 'UPS System Status',
                                  'decode': None},
            'mib_battery_status': {'iso': 'iso.3.6.1.4.1.318.1.1.1.2.1.1.0',
                                   'name': 'Battery Status',
                                   'decode': {'1': 'Unknown',
                                              '2': 'Battery Normal',
                                              '3': 'Battery Low',
                                              '4': 'Battery in Fault Condition'}},
            'mib_time_on_battery': {'iso': 'iso.3.6.1.4.1.318.1.1.1.2.1.2.0',
                                    'name': 'Time on Battery',
                                    'decode': None},
            'mib_battery_runtime_remain': {'iso': 'iso.3.6.1.4.1.318.1.1.1.2.2.3.0',
                                           'name': 'Runtime Remaining',
                                           'decode': None},
            'mib_battery_replace ': {'iso': 'iso.3.6.1.4.1.318.1.1.1.2.2.4.0',
                                     'name': 'Battery Replacement',
                                     'decode': {'1': 'OK',
                                                '2': 'Replacement Required'}},
            'mib_input_voltage': {'iso': 'iso.3.6.1.4.1.318.1.1.1.3.2.1.0',
                                  'name': 'Input Voltage',
                                  'decode': None},
            'mib_input_frequency': {'iso': 'iso.3.6.1.4.1.318.1.1.1.3.2.4.0',
                                    'name': 'Input Frequency Hz',
                                    'decode': None},
            'mib_reason_for_last_transfer': {'iso': 'iso.3.6.1.4.1.318.1.1.1.3.2.5.0',
                                             'name': 'Last Transfer Event',
                                             'decode': {'1': 'No Transfer',
                                                        '2': 'High Line Voltage',
                                                        '3': 'Brownout',
                                                        '4': 'Loss of Main Power',
                                                        '5': 'Small Temp Power Drop',
                                                        '6': 'Large Temp Power Drop',
                                                        '7': 'Small Spike',
                                                        '8': 'Large Spike',
                                                        '9': 'UPS Self Test',
                                                        '10': 'Excessive Input V Fluctuation'}},
            'mib_output_voltage': {'iso': 'iso.3.6.1.4.1.318.1.1.1.4.2.1.0',
                                   'name': 'Output Voltage',
                                   'decode': None},
            'mib_output_frequency': {'iso': 'iso.3.6.1.4.1.318.1.1.1.4.2.2.0',
                                     'name': 'Output Frequency Hz',
                                     'decode': None},
            'mib_output_load': {'iso': 'iso.3.6.1.4.1.318.1.1.1.4.2.3.0',
                                'name': 'Output Load as % of Capacity',
                                'decode': None},
            'mib_output_power': {'iso': 'iso.3.6.1.4.1.318.1.1.1.4.2.8.0',
                                 'name': 'Output Power in W',
                                 'decode': None},
            'mib_output_current': {'iso': 'iso.3.6.1.4.1.318.1.1.1.4.2.4.0',
                                   'name': 'Output Current in Amps',
                                   'decode': None},
            'mib_comms': {'iso': 'iso.3.6.1.4.1.318.1.1.1.8.1.0',
                          'name': 'Communicating with UPS Device',
                          'decode': {'1': 'Communication OK',
                                     '2': 'Communication Error'}},
            'mib_last_self_test_result': {'iso': 'iso.3.6.1.4.1.318.1.1.1.7.2.3.0',
                                          'name': 'Last Self Test Results',
                                          'decode': {'1': 'OK',
                                                     '2': 'Failed',
                                                     '3': 'Invalid',
                                                     '4': 'In Progress'}},
            'mib_last_self_test_date': {'iso': 'iso.3.6.1.4.1.318.1.1.1.7.2.4.0',
                                        'name': 'Date of Last Self Test',
                                        'decode': None}},
        # MiBs for Eaton UPS with PowerWalker NMC
        MIB_nmc.eaton_pw: {
            'mib_ups_info': {'iso': 'iso.3.6.1.2.1.1.1.0',
                             'name': 'General UPS Information',
                             'decode': None},
            'mib_ups_manufacturer': {'iso': 'iso.3.6.1.4.1.935.10.1.1.1.1.0',
                                     'name': 'UPS Manufacturer',
                                     'decode': None},
            'mib_firmware_revision': {'iso': 'iso.3.6.1.4.1.935.10.1.1.1.6.0',
                                      'name': 'UPS Firmware Revision',
                                      'decode': None},
            'mib_ups_type': {'iso': 'iso.3.6.1.4.1.935.10.1.1.1.2.0',
                             'name': 'UPS Model Type',
                             'decode': None},
            'mib_ups_contact': {'iso': 'iso.3.6.1.2.1.1.4.0',
                                'name': 'UPS Contact',
                                'decode': None},
            'mib_ups_location': {'iso': 'iso.3.6.1.2.1.1.6.0',
                                 'name': 'UPS Location',
                                 'decode': None},
            'mib_ups_uptime': {'iso': 'iso.3.6.1.2.1.1.3.0',
                               'name': 'System Up Time',
                               'decode': None},
            'mib_ups_manufacture_date': {'iso': 'iso.3.6.1.4.1.318.1.1.1.1.2.2.0',
                                         'name': 'UPS Manufacture Date',
                                         'decode': None},
            'mib_ups_name': {'iso': 'iso.3.6.1.2.1.33.1.1.5.0',
                             'name': 'UPS Name',
                             'decode': None},
            'mib_battery_capacity': {'iso': 'iso.3.6.1.4.1.935.10.1.1.3.4.0',
                                     'name': 'Percentage of Total Capacity',
                                     'decode': None},
            'mib_system_temperature': {'iso': 'iso.3.6.1.4.1.935.10.1.1.2.2.0',
                                       'name': 'System Temperature in C',
                                       'decode': None},
            'mib_system_status': {'iso': 'iso.3.6.1.4.1.935.10.1.1.3.1.0',
                                  'name': 'UPS System Status',
                                  'decode': {'1': 'Power On',
                                             '2': 'Standby',
                                             '3': 'Bypass',
                                             '4': 'Line',
                                             '5': 'Battery',
                                             '6': 'Battery Test',
                                             '7': 'Fault',
                                             '8': 'Converter',
                                             '9': 'ECO',
                                             '10': 'Shutdown',
                                             '11': 'On Booster',
                                             '12': 'On Reducer',
                                             '13': 'Other'}},
            'mib_battery_status': {'iso': 'iso.3.6.1.4.1.935.10.1.1.3.1.0',
                                   'name': 'Battery Status',
                                   'decode': {'1': 'Unknown',
                                              '2': 'Battery Normal',
                                              '3': 'Battery Low',
                                              '4': 'Battery Depleted',
                                              '5': 'Battery Discharging',
                                              '6': 'Battery Failure'}},
            'mib_time_on_battery': {'iso': 'iso.3.6.1.4.1.935.10.1.1.3.2.0',
                                    'name': 'Time on Battery',
                                    'decode': None},
            'mib_battery_runtime_remain': {'iso': 'iso.3.6.1.4.1.935.10.1.1.3.3.0',
                                           'name': 'Runtime Remaining',
                                           'decode': None},
            'mib_input_voltage': {'iso': 'iso.3.6.1.4.1.935.10.1.1.2.16.1.3.1',
                                  'name': 'Input Voltage V',
                                  'decode': None},
            'mib_input_frequency': {'iso': 'iso.3.6.1.4.1.935.10.1.1.2.16.1.2.1',
                                    'name': 'Input Frequency Hz',
                                    'decode': None},
            'mib_output_voltage': {'iso': 'iso.3.6.1.4.1.935.10.1.1.2.18.1.3.1',
                                   'name': 'Output Voltage',
                                   'decode': None},
            'mib_output_frequency': {'iso': 'iso.3.6.1.4.1.935.10.1.1.2.18.1.2.1',
                                     'name': 'Output Frequency Hz',
                                     'decode': None},
            'mib_output_load': {'iso': 'iso.3.6.1.4.1.935.10.1.1.2.18.1.7.1',
                                'name': 'Output Load as % of Capacity',
                                'decode': None},
            'mib_output_current': {'iso': 'iso.3.6.1.4.1.935.10.1.1.2.18.1.4.1',
                                   'name': 'Output Current in Amps',
                                   'decode': None},
            'mib_output_power': {'iso': 'iso.3.6.1.4.1.935.10.1.1.2.18.1.5.1',
                                 'name': 'Output Power in W',
                                 'decode': None},
            'mib_last_self_test_result': {'iso': 'iso.3.6.1.4.1.935.10.1.1.7.3.0',
                                          'name': 'Last Self Test Results',
                                          'decode': {'1': 'Idle',
                                                     '2': 'Processing',
                                                     '3': 'No Failure',
                                                     '4': 'Failure/Warning',
                                                     '5': 'Not Possible',
                                                     '6': 'Test Cancel'}},
            'mib_last_self_test_date': {'iso': 'iso.3.6.1.4.1.935.10.1.1.7.4.0',
                                        'name': 'Date of Last Self Test',
                                        'decode': None}}}
    # Check if snmp tools are installed
    _snmp_command: str = shutil.which('snmpget')
    _fatal: bool = False
    if not _snmp_command:
        print('Missing dependency: `sudo apt install snmp`')
        _fatal = True

    def __init__(self, ups_item: UpsItem):
        """
        Initialize mechanism to communicate with UPS via SNMP V2.
        """
        self.snmp_command = self._snmp_command

    @staticmethod
    def is_valid_ip_fqdn(test_value: str) -> bool:
        """
        Check if given string is a valid IP address of FQDN.

        :param test_value: String to be tested.
        :return:  True if valid
        """
        if not re.search(env.UT_CONST.PATTERNS['IPV4'], test_value):
            if not re.search(env.UT_CONST.PATTERNS['FQDN'], test_value):
                if not re.search(env.UT_CONST.PATTERNS['IPV6'], test_value):
                    env.UT_CONST.process_message('ERROR: IP Address entry [{}]'.format(test_value), verbose=True)
                    return False
        return True

    def get_mib_commands(self, nmc_type: MIB_nmc) -> dict:
        """ Get the list of MIB commands for the target UPS.

        :param nmc_type:
        :return: List of MIB commands for target UPS
        """
        if not tups:
            tups = self.active_ups
        return tups['mib_commands']

    def get_mib_name(self, mib_cmd: str, tups: dict = None) -> str:
        """Get the mib command name.

        :param mib_cmd: string representing mib command
        :param tups: target UPS, active UPS if missing
        :return: string of mib command name
        """
        if not tups:
            tups = self.active_ups
        if mib_cmd in tups['mib_commands'].keys():
            return tups['mib_commands'][mib_cmd]['name']
        return mib_cmd

    def get_mib_name_for_type(self, mib_cmd: str, ups_type: str) -> str:
        """Get mib command name for a given UPS type.

        :param mib_cmd:
        :param ups_type:
        :return: string of mib command name
        """
        return self.all_mib_cmds[ups_type][mib_cmd]['name']

    # Set of methods to check if UPS is valid.
    def check_ip_access(self, ip_fqdn: str, validate: bool = False) -> bool:
        """ Check the IP address value for the target UPS or active UPS if target is None.

        :param ip_fqdn:  The target ups dictionary from list or None.
        :param validate:  Validate if legal value provided if True.
        :return:  True if the given IP address is pingable, else False
        """
        if not ip_fqdn: return False
        if validate:
            if not self.is_valid_ip_fqdn(ip_fqdn): return False
        return not bool(os.system('ping -c 1 {} > /dev/null'.format(ip_fqdn)))

    def check_snmp_response(self, ups: UpsItem) -> bool:
        """ Check if the IP address for the target UPS, responds to snmp command.

        :param ups:  The target ups dictionary from list or None.
        :return:  True if the given IP address responds, else False
        """
        cmd_str = '{} -v2c -c {} {} {}'.format(self.snmp_command, ups.prm['snmp_community'],
                                               ups.prm['ups_IP'], 'iso.3.6.1.2.1.1.1.0')

        try:
            snmp_output = subprocess.check_output(shlex.split(cmd_str), shell=False,
                                                  stderr=subprocess.DEVNULL).decode().split('\n')
            LOGGER.debug(snmp_output)
        except subprocess.CalledProcessError as err:
            LOGGER.debug('%s execution error: %s', cmd_str, err)
            return False
        return True

    # Commands to read from UPS using snmp protocol.
    def get_monitor_mib_commands(self, cmd_type: str = 'all') -> Tuple[str, ...]:
        """ Get the specified list of monitor mib commands for the active UPS.

        :param cmd_type:  The target type of monitor commands
        :return:  list of relevant mib commands
        """
        if cmd_type == 'all':
            return_list = []
            for try_cmd_type in ['static', 'dynamic']:
                for item in self.monitor_mib_cmds[try_cmd_type]:
                    return_list.append(item)
            return tuple(return_list)
        return self.monitor_mib_cmds[cmd_type]

    def read_ups_list_items(self, command_list: list, ups: UpsItem, display: bool = False) -> dict:
        """ Read the specified list of monitor mib commands for specified UPS.

        :param command_list:  A list of mib commands to be read from the specified UPS.
        :param ups:  The target ups dictionary from list or None.
        :param display: Flag to indicate if parameters should be displayed as read.
        :return:  dict of results from the reading of all commands target UPS.
        """
        if not tups:
            tups = self.active_ups
        results = {'valid': True,
                   'display_name': self.ups_name(tups=tups),
                   'name': self.ups_name(tups=tups),
                   'uuid': self.ups_uuid(tups=tups),
                   'ups_IP': self.ups_ip(tups=tups),
                   'ups_nmc_model': self.ups_nmc_model(tups=tups),
                   'ups_type': self.ups_type(tups=tups)}
        for cmd in command_list:
            results[cmd] = self.send_snmp_command(cmd, tups=tups, display=display)
            if not results[cmd]:
                results['valid'] = False
                break
            if cmd == 'mib_ups_info':
                if results['ups_type'] == 'apc-ap96xx':
                    try:
                        results['ups_nmc_model'] = re.sub(r'.*MN:', '', results[cmd]).split()[0]
                        tups['ups_nmc_model'] = results['ups_nmc_model']
                    except(KeyError, IndexError):
                        results['ups_nmc_model'] = self.ups_type(tups=tups)
                else:
                    results['ups_nmc_model'] = self.ups_type(tups=tups)
        # Since PowerWalker NMC is not intended for 110V UPSs, the following correction to output current is needed.
        if self.ups_type(tups=tups) == 'eaton-pw':
            if 'mib_output_current' in results.keys() and 'mib_output_voltage' in results.keys():
                results['mib_output_current'] = round((230 / results['mib_output_voltage']) *
                                                      results['mib_output_current'], 1)
        return results

    def send_snmp_command(self, command_name: str, tups: dict = None,
                          display: bool = False) -> Union[str, int, List[Union[float, str]], float, None]:
        """ Read the specified mib commands results for specified UPS or active UPS if not specified.

        :param command_name:  A command to be read from the target UPS
        :param tups:  The target ups dictionary from list or None.
        :param display: If true the results will be printed
        :return:  The results from the read, could be str, int or tuple
        """
        if not tups:
            tups = self.active_ups
        if not self.is_responsive(tups):
            return 'Invalid UPS'
        snmp_mib_commands = self.get_mib_commands(tups)
        if command_name not in snmp_mib_commands:
            return 'No data'
        if command_name == 'mib_ups_env_temp' and self.ups_nmc_model(tups=tups) != 'AP9641':
            return 'No data'
        cmd_mib = snmp_mib_commands[command_name]['iso']
        cmd_str = 'snmpget -v2c -c {} {} {}'.format(tups['snmp_community'], tups['ups_IP'], cmd_mib)
        try:
            snmp_output = subprocess.check_output(shlex.split(cmd_str), shell=False,
                                                  stderr=subprocess.DEVNULL).decode().split('\n')
        except subprocess.CalledProcessError:
            LOGGER.debug('Error executing snmp %s command [%s] to %s at %s.', command_name, cmd_mib, self.ups_name(),
                         self.ups_ip())
            return None

        value = ''
        value_minute = -1
        value_str = 'UNK'
        for line in snmp_output:
            if not line: continue
            LOGGER.debug('line: %s', line)
            if re.match(env.UT_CONST.PATTERNS['SNMP_VALUE'], line):
                value = line.split(':', 1)[1]
                value = re.sub(r'\"', '', value).strip()
        if snmp_mib_commands[command_name]['decode']:
            if value in snmp_mib_commands[command_name]['decode'].keys():
                value = snmp_mib_commands[command_name]['decode'][value]
        if tups['ups_type'] == 'eaton-pw':
            if command_name == 'mib_output_voltage' or command_name == 'mib_output_frequency':
                value = int(value) / 10.0
            elif command_name == 'mib_output_current':
                value = int(value) / 10.0
            elif command_name == 'mib_input_voltage' or command_name == 'mib_input_frequency':
                value = int(value) / 10.0
            elif command_name == 'mib_system_temperature':
                value = int(value) / 10.0
        if command_name == 'mib_system_status' and tups['ups_type'] == 'apc-ap96xx':
            value = self.bit_str_decoder(value, self.decoders['apc_system_status'])
        if command_name == 'mib_time_on_battery' or command_name == 'mib_battery_runtime_remain':
            # Create a minute, string tuple
            if tups['ups_type'] == 'eaton-pw':
                # Process time for eaton-pw
                if command_name == 'mib_time_on_battery':
                    # Measured in seconds.
                    value = int(value)
                else:
                    # Measured in minutes.
                    value = int(value) * 60
                value_str = str(datetime.timedelta(seconds=int(value)))
                value_minute = round(float(value) / 60.0, 2)
                value = [value_minute, value_str]
            else:
                # Process time for apc
                value_items = re.sub(r'\(', '', value).split(')')
                if len(value_items) >= 2:
                    value_minute, value_str = value_items
                value = (round(int(value_minute) / 60 / 60, 2), value_str)
        if display:
            if command_name == 'mib_output_current' and tups['ups_type'] == 'eaton-pw':
                print('{}: {} - raw, uncorrected value.'.format(snmp_mib_commands[command_name]['name'], value))
            else:
                print('{}: {}'.format(snmp_mib_commands[command_name]['name'], value))
        return value

    @staticmethod
    def bit_str_decoder(value: str, decode_key: tuple) -> str:
        """ Bit string decoder

        :param value: A string representing a bit encoded set of flags
        :param decode_key: A list representing the meaning of a 1 for each bit field
        :return: A string of concatenated bit decode strings
        """
        value_str = ''
        for index, bit_value in enumerate(value):
            if index > len(decode_key):
                break
            if bit_value == '1':
                if value_str == '':
                    value_str = decode_key[index]
                else:
                    value_str = '{}-{}'.format(value_str, decode_key[index])
        return value_str

    def print_decoders(self) -> None:
        """ Prints all bit decoders.

        :return: None
        """
        for decoder_name, decoder_list in self.decoders.items():
            print('decode key: {}'.format(decoder_name))
            for i, item in enumerate(decoder_list, start=1):
                print('  {:2d}: {}'.format(i, item))

    def print_snmp_commands(self, tups: dict = None) -> None:
        """ Print all supported mib commands for the target UPS, which is the active UPS when not specified.

        :param tups:  The target ups dictionary from list or None.
        :return:  None
        """
        if not tups:
            tups = self.active_ups
        for mib_name, mib_dict in self.get_mib_commands(tups).items():
            print('{}: Value: {}'.format(mib_name, mib_dict['iso']))
            print('    Description: {}'.format(mib_dict['name']))
            if mib_dict['decode']:
                for decoder_name, decoder_list in mib_dict['decode'].items():
                    print('        {}: {}'.format(decoder_name, decoder_list))
    # End of commands to read from UPS using snmp protocol.

