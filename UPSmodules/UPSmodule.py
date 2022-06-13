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

    @classmethod
    def list(cls):
        return list(map(lambda c: c.name, cls))


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
    _json_keys: Set[str] = {'ups_IP', 'display_name', 'ups_type', 'daemon', 'snmp_community', 'uuid', 'ups_model'}
    UPS_type: UpsEnum = UpsEnum('type', 'all apc_ap96xx eaton_pw')

    def __init__(self, json_details: dict):
        # UPS list from ups-config.json for monitor and ls utils.
        self.prm: ObjDict = ObjDict({
            'uuid': None,
            'ups_IP': None,
            'display_name': None,
            'ups_type': None,
            'ups_model': None,
            'ups_nmc_model': None,
            'snmp_community': None,
            'daemon': False,
            'valid': False,
            'compatible': False,
            'accessible': False,
            'responsive': False})

        # Load initial data from json dict.
        for item_name, item_value in json_details.items():
            if item_name not in self._json_keys:
                LOGGER.debug('%s: Invalid key [%s] ignored', env.UT_CONST.ups_json_file, item_name)
                continue
            self.prm[item_name] = item_value

        if self.prm['ups_type'] in UpsComm.MIB_nmc.list():
            self.prm['compatible'] = True

        # Check accessibility
        self.ups_comm: UpsComm = UpsComm(self)
        if self.ups_comm.is_valid_ip_fqdn(self.prm['ups_IP']):
            self.prm['valid'] = True
        if self.ups_comm.check_ip_access(self.prm['ups_IP']):
            self.prm['accessible'] = True
        if self.ups_comm.check_snmp_response(self):
            self.prm['responsive'] = True

        self.prm['mib_commands'] = self.ups_comm.all_mib_cmds[self.prm['ups_type']]
        if self.prm['daemon']:
            self.daemon = UpsDaemon()

    def __str__(self):
        str_rep = ''
        for name, value in self.prm.items():
            if name == 'mib_commands': continue
            if str_rep:
                str_rep = '{}\n{}: {}'.format(str_rep, name, value)
            else:
                str_rep = '{}: {}'.format(name, value)
            #for ups_param_name, ups_param_value in value.items():
                #str_rep = '{}\n    {}: {}\n'.format(str_rep, ups_param_name, ups_param_value)
        return str_rep

    def mib_command_names(self, cmd_group: UpsEnum) -> Generator[str, None, None]:
        if cmd_group == UpsComm.MIB_group.all:
            if self.prm.ups_type == UpsComm.MIB_nmc.apc_ap96xx:
                cmd_group = UpsComm.MIB_group.all_apc
            else:
                cmd_group = UpsComm.MIB_group.all_eaton
        for cmd_name in self.ups_comm.mib_commands:
            if cmd_name in UpsComm.all_mib_cmd_names[cmd_group]:
                yield cmd_name

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

    def ups_type(self) -> UpsEnum:
        """ Get the type value for the target UPS or active UPS if target is None.

        :return:  The ups_type as a UpsEnum.
        """
        return self.prm['ups_type']

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

    def is_compatible(self):
        return self.prm.compatible

    def is_accessible(self):
        return self.prm.accessible

    def is_responsive(self):
        return self.prm.responsive

    def send_snmp_command(self, cmd_name: str, display: bool = True) -> str:
        return self.ups_comm.send_snmp_command(cmd_name, self, display)

    def read_ups_list_items(self, command_list: list, display: bool = False) -> dict:
        return self.ups_comm.read_ups_list_items(command_list, self, display=display)


class UpsDaemon:
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

    Text_style: UpsEnum = UpsEnum('style', 'warn crit green bold normal')

    def __init__(self):
        self.config: Union[dict, None] = None
        self.daemon_ups: Union[UpsItem, None] = None

        self.read_daemon_config()
        self.set_daemon_parameters()

    def read_daemon_config(self) -> bool:
        """

        :return:
        """
        if not env.UT_CONST.ups_config_ini:
            print('Error ups-utils.ini filename not set.')
            return False

        self.config = configparser.ConfigParser()
        try:
            self.config.read(env.UT_CONST.ups_config_ini)
        except configparser.Error as err:
            LOGGER.exception('config parser error: %s', err)
            print('Error in ups-utils.ini file.  Using defaults')
            return False
        LOGGER.debug('config[DaemonPaths]: %s', dict(self.config['DaemonPaths']))
        LOGGER.debug('config[DaemonScripts]: %s', dict(self.config['DaemonScripts']))
        LOGGER.debug('config[DaemonParameters]: %s', dict(self.config['DaemonParameters']))
        return True

    def set_daemon_parameters(self) -> bool:
        """ Set all daemon parameters based on defaults in env.UT_CONST and the config.py file.

        :return:  True on success
        """
        read_status = True

        # Set path definitions
        for path_name in self.daemon_paths:
            if isinstance(self.config['DaemonPaths'][path_name], str):
                self.daemon_params[path_name] = os.path.expanduser(self.config['DaemonPaths'][path_name])
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
            if isinstance(self.config['DaemonScripts'][script_name], str):
                self.daemon_params[script_name] = os.path.join(self.daemon_params['ups_utils_script_path'],
                                                               self.config['DaemonScripts'][script_name])
                if self.daemon_params[script_name]:
                    if not os.path.isfile(self.daemon_params[script_name]):
                        print('Missing {} script: {}'.format(script_name, self.daemon_params[script_name]))
                        read_status = False

        # Set script parameters
        for parameter_name in self.daemon_param_names:
            if re.search(env.UT_CONST.PATTERNS['INI'], self.config['DaemonParameters'][parameter_name]):
                raw_param = re.sub(r'\s+', '', self.config['DaemonParameters'][parameter_name])
                params = tuple(int(x) for x in raw_param[1:-1].split(','))
                if parameter_name == 'read_interval':
                    self.daemon_params[parameter_name]['monitor'] = params[0]
                    self.daemon_params[parameter_name]['daemon'] = params[1]
                else:
                    self.daemon_params[parameter_name]['crit'] = params[0]
                    self.daemon_params[parameter_name]['warn'] = params[1]
            else:
                LOGGER.debug('Incorrect format for %s parameter: %s',
                             parameter_name, self.config['DaemonParameters'][parameter_name])
                print('Incorrect format for {} parameter: {}'.format(
                    parameter_name, self.config['DaemonParameters'][parameter_name]))
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

    @classmethod
    def print_daemon_parameters(cls) -> None:
        """ Print all daemon parameters.
        """
        print('Daemon parameters:')
        for param_name, param_value in cls.daemon_params.items():
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
    def __init__(self, daemon: bool = True):
        self.list: Dict[str, UpsItem] = {}
        self.daemon: Union[UpsDaemon, None] = UpsDaemon() if daemon else None
        self.read_ups_json()

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
        Get uuid, gpu pairs from a UpsList object.

        :return:  uuid, ups pair
        """
        for key, value in self.list.items():
            yield key, value

    def uuids(self) -> Generator[str, None, None]:
        """
        Get uuids of the UpsList object.

        :return: uuids from the UpsList object.
        """
        for key in self.list:
            yield key

    def upss(self) -> Generator[UpsItem, None, None]:
        """
        Get UpsItems from a GpuList object.

        :return: UpsItem
        """
        return self.__iter__()

    def add(self, ups_item: UpsItem) -> None:
        """
        Add given UpsItem to the UpsList.

        :param ups_item:  Item to be added
        """
        self[ups_item.prm.uuid] = ups_item
        LOGGER.debug('Added UPS Item %s to UPS List', ups_item.prm.uuid)

    def print_daemon_parameters(self) -> None:
        self.daemon.set_daemon_parameters()

    def print(self) -> None:
        for ups in self.upss():
            print(ups, '\n')

    def num_upss(self, ups_type: UpsEnum = UpsItem.UPS_type.all) -> Dict[str, int]:
        """
        Return the count of UPSs by total, accessible, compatible, responsive, and valid.

        :param ups_type: Only count UPSs of specific ups_type or all ups_type by default.
        :return: Dictionary of UPS counts
        """
        try:
            ups_type_name = ups_type.name
        except AttributeError as error:
            raise AttributeError('Error: {} not a valid vendor name: [{}]'.format(ups_type, UpsItem.UPS_type)) from error
        results_dict = {'ups_type': ups_type_name, 'total': 0,
                        'accessible': 0, 'compatible': 0, 'responsive': 0, 'valid': 0}
        for ups in self.upss():
            if ups_type != UpsItem.UPS_type.all:
                if ups_type != ups.prm.ups_type:
                    continue
            if ups.prm.valid:
                results_dict['valid'] += 1
            if ups.prm.accessible:
                results_dict['accessible'] += 1
            if ups.prm.compatible:
                results_dict['compatible'] += 1
            if ups.prm.responsive:
                results_dict['responsive'] += 1
            results_dict['total'] += 1
        return results_dict

    def read_all_ups_list_items(self, command_list: Union[List[str], Tuple[str]], errups: bool = True,
                                display: bool = False) -> dict:
        """ Get the specified list of monitor mib commands for all UPSs.

        :param command_list:  A list of mib commands to be read from the all UPSs.
        :param errups: Flag to indicate if error UPS should be included.
        :param display: Flag to indicate if parameters should be displayed as read.
        :return:  dict of results from the reading of all commands from all UPSs.
        """
        results = {}
        for uuid, ups in self.items():
            if not errups:
                if not ups.prm.responsive:
                    continue
            results[uuid] = ups.read_ups_list_items(command_list, display=display)
            return results

    def read_ups_json(self) -> bool:
        """ Reads the ups-config.json file which contains parameters for UPSs to be used by utility.
            Build of list of UpsItems representing each of the UPSs defined in the json file.

        :return: boolean True if no problems reading list
        """
        if not env.UT_CONST.ups_json_file or not os.path.isfile(env.UT_CONST.ups_json_file):
            print('Error: {} file not found: {}'.format(os.path.basename(env.UT_CONST.ups_json_file),
                                                        env.UT_CONST.ups_json_file))
            return False
        try:
            with open(env.UT_CONST.ups_json_file, 'r') as ups_list_file:
                ups_items = json.load(ups_list_file)
        except FileNotFoundError as error:
            env.UT_CONST.process_message("Error: file not found error for [{}]: {}".format(
                env.UT_CONST.ups_json_file, error), verbose=True)
            return False
        except PermissionError as error:
            env.UT_CONST.ups_json_file("Error: permission error for [{}]: {}".format(
                env.UT_CONST.ups_json_file, error), verbose=True)
            return False
        for ups in ups_items.values():
            uuid = uuid4().hex
            ups['uuid'] = uuid
            ups['ups_nmc_model'] = ups['ups_type']
            self.list[uuid] = UpsItem(ups)
        return True

    # Methods to get, check, and list UPSs
    def get_name_for_ups_uuid(self, ups_uuid: int) -> str:
        """ Get the ups name for a given uuid

        :param ups_uuid: Universally unique identifier for a UPS
        :return: name of the ups
        """
        for name, ups in self.upss():
            if ups['uuid'] == ups_uuid:
                return ups['display_name']
        return 'Error'

    def get_uuid_for_ups_name(self, ups_name: str) -> str:
        """ Get uuid for ups with given name.

        :param ups_name: The target ups name.
        :return: The uuid as str or 'Error' if not found
        """
        for ups in self.upss():
            if ups['display_name'] == ups_name:
                return ups['uuid']
        return 'Error'

    def get_ups_type_list(self) -> Tuple[UpsEnum]:
        """
        Get a tuple of unique ups types.

        :return:
        """
        type_list: List[UpsEnum] = []
        for ups in self.upss():
            if ups.prm.ups_type not in type_list:
                type_list.append(ups.prm.ups_type)
        return tuple(type_list)

    def get_ups_list(self, errups: bool = True) -> dict:
        """Get the dictionary list of UPSs read at start up.

        :param errups: Flag to indicate if UPSs with errors should be included
        :return:  dictionary representing the list of UPSs
        """
        return_list = {}
        for uuid, ups in self.items():
            if not errups:
                if not ups.prm.responsive:
                    continue
            return_list[uuid] = ups
        return return_list

    def get_num_ups_tuple(self) -> Tuple[int]:
        """ This function will return a tuple of the UPS counts.

        :return: tuple represents listed, compatible, accessible, responsive UPSs
        """
        cnt = [0, 0, 0, 0]
        for ups in self.upss():
            cnt[0] += 1
            if ups.prm.compatible:
                cnt[1] += 1
            if ups.prm.accessible:
                cnt[2] += 1
            if ups.prm.responsive:
                cnt[3] += 1
        return tuple(cnt)


class UpsComm:
    """ Class definition for UPS communication object."""

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

    MIB_nmc: UpsEnum = UpsEnum('nmc', 'all apc_ap96xx eaton_pw')
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

    # UPS MiB Commands lists
    _mib_all_apc_ap96xx = tuple(all_mib_cmds[MIB_nmc.apc_ap96xx].keys())
    _mib_all_eaton_pw = tuple(all_mib_cmds[MIB_nmc.eaton_pw].keys())
    _mib_static: Tuple[str, ...] = ('mib_ups_name', 'mib_ups_info', 'mib_bios_serial_number', 'mib_firmware_revision',
                                    'mib_ups_type', 'mib_ups_location', 'mib_ups_uptime')
    _mib_dynamic: Tuple[str, ...] = ('mib_ups_env_temp', 'mib_battery_capacity', 'mib_time_on_battery',
                                     'mib_battery_runtime_remain', 'mib_input_voltage', 'mib_input_frequency',
                                     'mib_output_voltage', 'mib_output_frequency', 'mib_output_load',
                                     'mib_output_current', 'mib_output_power', 'mib_system_status',
                                     'mib_battery_status')
    _mib_output: Tuple[str, ...] = ('mib_output_voltage', 'mib_output_frequency', 'mib_output_load',
                                    'mib_output_current', 'mib_output_power')
    _mib_input: Tuple[str, ...] = ('mib_input_voltage', 'mib_input_frequency')
    MIB_group: UpsEnum = UpsEnum('group', 'all all_apc all_eaton monitor static dynamic input output')
    all_mib_cmd_names: Dict[MIB_group, Tuple[str, ...]] = {
        MIB_group.all:       _mib_all_apc_ap96xx,   # I choose all to be apc since eaton is a subset of apc.
        MIB_group.all_apc:   _mib_all_apc_ap96xx,
        MIB_group.all_eaton: _mib_all_eaton_pw,
        MIB_group.monitor:   _mib_dynamic + _mib_static,
        MIB_group.output:    _mib_output,
        MIB_group.input:     _mib_input,
        MIB_group.static:    _mib_static,
        MIB_group.dynamic:   _mib_dynamic}
    # MIB Command Lists

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
        self.daemon: bool = ups_item.prm.daemon
        if ups_item.prm['ups_type'] in self.MIB_nmc.list():
            self.ups_type = ups_item.prm['ups_type'] = self.MIB_nmc[ups_item.prm['ups_type']]
        self.mib_commands = self.all_mib_cmds[self.ups_type]

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

    #@staticmethod
    #def get_mib_commands(ups: UpsItem) -> dict:
        #""" Get the list of MIB commands for this UPS.
#
        #:param ups:
        #:return: List of MIB commands for target UPS
        #"""
        #return ups.prm['mib_commands']

    @classmethod
    def get_mib_name(cls, mib_cmd: str) -> str:
        """Get the mib command name.

        :param mib_cmd: string representing mib command
        :return: string of mib command name
        """
        if mib_cmd in cls.all_mib_cmds[UpsComm.MIB_nmc.apc_ap96xx].keys():
            return cls.all_mib_cmds[UpsComm.MIB_nmc.apc_ap96xx][mib_cmd]['name']
        return mib_cmd

    #def get_mib_name_for_type(self, mib_cmd: str, ups_type: str) -> str:
        #"""Get mib command name for a given UPS type.
#
        #:param mib_cmd:
        #:param ups_type:
        #:return: string of mib command name
        #"""
        #return self.all_mib_cmds[ups_type][mib_cmd]['name']

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
    def get_monitor_mib_commands(self, cmd_group: MIB_group = MIB_group.dynamic) -> Tuple[str, ...]:
        """ Get the specified list of monitor mib commands for the active UPS.

        :param cmd_group:  The target group of monitor commands
        :return:  list of relevant mib commands
        """
        try:
            _ = cmd_group.name
        except AttributeError as error:
            raise AttributeError('Error: {} not a valid group name: [{}]'.format(
                cmd_group, self.MIB_group)) from error
        return UpsComm.all_mib_cmd_names[cmd_group]

    def read_ups_list_items(self, command_list: list, ups: UpsItem, display: bool = False) -> dict:
        """ Read the specified list of monitor mib commands for specified UPS.

        :param command_list:  A list of mib commands to be read from the specified UPS.
        :param ups:  The target ups item
        :param display: Flag to indicate if parameters should be displayed as read.
        :return:  dict of results from the reading of all commands target UPS.
        """
        results = {'valid': True,
                   'display_name': ups.ups_name(),
                   'name': ups.ups_name(),
                   'uuid': ups.ups_uuid(),
                   'ups_IP': ups.ups_ip(),
                   'ups_nmc_model': ups.ups_nmc_model(),
                   'ups_type': ups.ups_type()}
        for cmd in command_list:
            results[cmd] = self.send_snmp_command(cmd, ups, display=display)
            if not results[cmd]:
                results['valid'] = False
                break
            if cmd == 'mib_ups_info':
                if results['ups_type'] == UpsComm.MIB_nmc.apc_ap96xx:
                    try:
                        results['ups_nmc_model'] = re.sub(r'.*MN:', '', results[cmd]).split()[0]
                        ups.prm['ups_nmc_model'] = results['ups_nmc_model']
                    except(KeyError, IndexError):
                        results['ups_nmc_model'] = ups.ups_type().name
                else:
                    results['ups_nmc_model'] = ups.ups_type().name
        # Since PowerWalker NMC is not intended for 110V UPSs, the following correction to output current is needed.
        if ups.prm.ups_type == UpsComm.MIB_nmc.eaton_pw:
            if 'mib_output_current' in results.keys() and 'mib_output_voltage' in results.keys():
                results['mib_output_current'] = round((230 / results['mib_output_voltage']) *
                                                      results['mib_output_current'], 1)
        return results

    def send_snmp_command(self, command_name: str, ups: UpsItem,
                          display: bool = False) -> Union[str, int, List[Union[float, str]], float, None]:
        """ Read the specified mib commands results for specified UPS or active UPS if not specified.

        :param command_name:  A command to be read from the target UPS
        :param ups:  The target ups item
        :param display: If true the results will be printed
        :return:  The results from the read, could be str, int or tuple
        """
        if not ups.prm['responsive']:
            return 'Invalid UPS'
        snmp_mib_commands = ups.prm.mib_commands
        if command_name not in snmp_mib_commands:
            return 'No data'
        if command_name == 'mib_ups_env_temp' and ups.ups_nmc_model() != 'AP9641':
            return 'No data'
        cmd_mib = snmp_mib_commands[command_name]['iso']
        cmd_str = 'snmpget -v2c -c {} {} {}'.format(ups.prm['snmp_community'],
                                                    ups.prm['ups_IP'], cmd_mib)
        try:
            snmp_output = subprocess.check_output(shlex.split(cmd_str), shell=False,
                                                  stderr=subprocess.DEVNULL).decode().split('\n')
        except subprocess.CalledProcessError:
            LOGGER.debug('Error executing snmp %s command [%s] to %s at %s.', command_name, cmd_mib, self.ups_name(),
                         ups.ups_ip())
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
        if ups.prm['ups_type'] == UpsComm.MIB_nmc.eaton_pw:
            if command_name == 'mib_output_voltage' or command_name == 'mib_output_frequency':
                value = int(value) / 10.0
            elif command_name == 'mib_output_current':
                value = int(value) / 10.0
            elif command_name == 'mib_input_voltage' or command_name == 'mib_input_frequency':
                value = int(value) / 10.0
            elif command_name == 'mib_system_temperature':
                value = int(value) / 10.0
        if command_name == 'mib_system_status' and ups.prm['ups_type'] == UpsComm.MIB_nmc.apc_ap96xx:
            value = self.bit_str_decoder(value, self.decoders['apc_system_status'])
        if command_name == 'mib_time_on_battery' or command_name == 'mib_battery_runtime_remain':
            # Create a minute, string tuple
            if ups.prm['ups_type'] == UpsComm.MIB_nmc.eaton_pw:
                # Process time for eaton_pw
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
            if command_name == 'mib_output_current' and ups.prm['ups_type'] == UpsComm.MIB_nmc.eaton_pw:
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

    @classmethod
    def print_decoders(cls) -> None:
        """ Prints all bit decoders.

        :return: None
        """
        for decoder_name, decoder_list in cls.decoders.items():
            print('decode key: {}'.format(decoder_name))
            for i, item in enumerate(decoder_list, start=1):
                print('  {:2d}: {}'.format(i, item))

    @staticmethod
    def print_snmp_commands(ups: UpsItem) -> None:
        """ Print all supported mib commands for the target UPS, which is the active UPS when not specified.

        :param ups:  The target ups dictionary from list or None.
        """
        for mib_name, mib_dict in ups.prm.get_mib_commands().items():
            print('{}: Value: {}'.format(mib_name, mib_dict['iso']))
            print('    Description: {}'.format(mib_dict['name']))
            if mib_dict['decode']:
                for decoder_name, decoder_list in mib_dict['decode'].items():
                    print('        {}: {}'.format(decoder_name, decoder_list))

