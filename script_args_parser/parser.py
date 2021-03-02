import os
import re
import shlex
from argparse import ArgumentParser
from dataclasses import dataclass
from typing import Any, Optional

import toml


@dataclass
class Argument:
    """
    Stores information about single script arguments
    """
    name: str  #: custom name that the value will be stored at
    type: str  #: one of supported types (see README.md)
    description: str  #: user friendly description
    cli_arg: str  #: name of the cli option to set value
    env_var: Optional[str] = None  #: name of the env var that will be used as a fallback when cli not set
    default_value: Optional[str] = None  #: default value if nothing else is set

    def __post_init__(self):
        self.is_list = False
        self.list_type: Optional[str] = None
        self.is_tuple = False
        self.tuple_types: Optional[list[str]] = None
        list_match = re.match(r'list\[(.+)\]', self.type)
        if list_match is not None:
            self.is_list = True
            self.list_type = list_match[1]
        tuple_match = re.search(r'tuple\[(.+?)\]', self.type)
        if tuple_match is not None:
            self.is_tuple = True
            self.tuple_types = [x.strip() for x in tuple_match[1].split(',')]

    @property
    def argparse_options(self) -> dict:
        """
        :return: args and kwargs that can be used in argparse.ArgumentParser.add_argument
        """
        args = [self.cli_arg]
        kwargs = {'dest': self.name}
        if self.type == 'switch':
            kwargs['action'] = 'store'
            kwargs['nargs'] = '?'
            kwargs['const'] = True
        if self.is_list:
            kwargs['action'] = 'append'
        if self.is_tuple:
            kwargs['nargs'] = len(self.tuple_types)
        return (args, kwargs)


def str_to_bool(value: str) -> bool:
    """
    Parses string into bool. It tries to match some predefined values.
    If none is matches, python bool(value) is used.

    :param value: string to be parsed into bool
    :return: bool value of a given string
    """
    if value in ['0', 'False']:
        return False
    if value in ['1', 'True']:
        return True
    return bool(value)


class ArgumentsParser:
    """
    Parses arguments according to given toml definition and cli parameters.
    Values for arguments are stored in arguments_values dictionary.

    :param arguments_definitions: toml string containing arguments definition
    :param cli_params: list of cli parameters, if not given sys.arg[1:] is used
    """
    TYPES_MAPPING = {
        'str': str,
        'int': int,
        'bool': str_to_bool,
        'switch': str_to_bool,
    }  #: Maps string values of types to actual converters

    def __init__(self, arguments_definitions: str, cli_params: Optional[list[str]] = None) -> None:
        self.arguments_definitions = arguments_definitions
        self.arguments = self._parse_toml_definitions()
        self.arguments_values = self._read_cli_arguments(cli_params)
        self._fallback_values()
        self._calculate_lists_and_tuples()
        self._convert_values()

    def _parse_toml_definitions(self) -> list[Argument]:
        parsed_toml = toml.loads(self.arguments_definitions)
        return [Argument(name=arg_name, **arg_def) for arg_name, arg_def in parsed_toml.items()]

    def _read_cli_arguments(self, cli_params: list[str] = None) -> dict[str, Any]:
        cli_parser = ArgumentParser()
        for argument in self.arguments:
            args, kwargs = argument.argparse_options
            cli_parser.add_argument(*args, **kwargs)
        return vars(cli_parser.parse_args(cli_params))

    def _fallback_values(self) -> None:
        for argument in self.arguments:
            if self.arguments_values[argument.name] is None and argument.env_var is not None:
                self.arguments_values[argument.name] = os.getenv(argument.env_var)
            if self.arguments_values[argument.name] is None and argument.default_value is not None:
                self.arguments_values[argument.name] = argument.default_value

    def _calculate_lists_and_tuples(self) -> None:
        for argument in self.arguments:
            argument_value = self.arguments_values[argument.name]
            if not argument.is_list and not argument.is_tuple:
                continue
            if argument_value is None or not isinstance(argument_value, str):
                continue
            if argument.is_list and not argument.is_tuple:
                self.arguments_values[argument.name] = self._parse_list(argument, argument_value)
            elif not argument.is_list and argument.is_tuple:
                self.arguments_values[argument.name] = self._parse_tuple(argument, argument_value)
            elif argument.is_list and argument.is_tuple:
                self.arguments_values[argument.name] = self._parse_list_of_tuples(argument, argument_value)

    def _convert_values(self) -> None:
        for argument in self.arguments:
            argument_value = self.arguments_values[argument.name]
            if argument_value is None:
                continue
            if argument.is_list and not argument.is_tuple:
                self.arguments_values[argument.name] = [
                    self.TYPES_MAPPING[argument.list_type](x) for x in argument_value
                ]
            elif not argument.is_list and argument.is_tuple:
                converters = [self.TYPES_MAPPING[x] for x in argument.tuple_types]
                self.arguments_values[argument.name] = [
                    conv(value) for conv, value in zip(converters, argument_value)
                ]
            elif argument.is_list and argument.is_tuple:
                converters = [self.TYPES_MAPPING[x] for x in argument.tuple_types]
                self.arguments_values[argument.name] = [
                    [conv(value) for conv, value in zip(converters, list_elem_value)]
                    for list_elem_value in argument_value
                ]
            else:
                self.arguments_values[argument.name] = self.TYPES_MAPPING[argument.type](argument_value)

    def _parse_tuple(self, argument: Argument, argument_value: str) -> list[Any]:
        ret_val = shlex.split(argument_value)
        if len(ret_val) == 0:
            return ['']
        expected_number = len(argument.tuple_types)
        actual_number = len(ret_val)
        if actual_number != expected_number:
            raise RuntimeError(
                f'Tuple {argument.name} expected {expected_number} values and got {actual_number}: '
                f'{argument_value}.'
            )
        return ret_val

    def _split_list(self, argument: Argument, argument_value: str) -> list[Any]:
        if argument_value == '':
            return ['']
        argument_value = ' ' + argument_value + ' '
        while argument_value.find(';;') != -1:
            argument_value = argument_value.replace(';;', '; ;', 1)
        parser = shlex.shlex(argument_value)
        parser.whitespace_split = True
        parser.whitespace = ';'
        return list(parser)

    def _parse_list(self, argument: Argument, argument_value: str) -> list[Any]:
        ret_val = []
        for value in self._split_list(argument, argument_value):
            parsed_value = shlex.split(value)
            if len(parsed_value) == 0:
                ret_val.append('')
            else:
                ret_val.append(parsed_value[0])
        return ret_val

    def _parse_list_of_tuples(self, argument: Argument, argument_value: str) -> list[Any]:
        ret_val = []
        for value in self._split_list(argument, argument_value):
            print(f'x{value}x')
            print(type(value))
            parsed_value = self._parse_tuple(argument, value)
            ret_val.append(parsed_value)
        return ret_val
