#!/usr/bin/env python
#
# soaplib - Copyright (C) Soaplib contributors.
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301
#

import unittest

from suds.client import Client
from suds import WebFault
from datetime import datetime

class TestSuds(unittest.TestCase):
    def setUp(self):
        self.client = Client("http://localhost:9753/?wsdl", cache=None)
        self.ns = "soaplib.test.interop.server._service"

    def test_echo_simple_boolean_array(self):
        val = [True, False, False, True]
        ret = self.client.service.echo_simple_boolean_array(val)

        assert val == ret

    def test_enum(self):
        DaysOfWeekEnum = self.client.factory.create("DaysOfWeekEnum")

        val = DaysOfWeekEnum.Monday
        ret = self.client.service.echo_enum(val)

        assert val == ret

    def test_validation(self):
        non_nillable_class = self.client.factory.create("{hunk.sunk}NonNillableClass")
        non_nillable_class.i = 6
        non_nillable_class.s = None

        try:
            ret = self.client.service.non_nillable(non_nillable_class)
            raise Exception("must fail")
        except WebFault, e:
            pass

    def test_echo_integer_array(self):
        ia = self.client.factory.create('integerArray')
        ia.integer.extend([1,2,3,4,5])
        self.client.service.echo_integer_array(ia)

    def test_echo_in_header(self):
        in_header = self.client.factory.create('InHeader')
        in_header.s = 'a'
        in_header.i = 3

        self.client.set_options(soapheaders=in_header)
        ret = self.client.service.echo_in_header()
        self.client.set_options(soapheaders=None)

        print ret

        self.assertEquals(in_header.s, ret.s)
        self.assertEquals(in_header.i, ret.i)

    def test_send_out_header(self):
        out_header = self.client.factory.create('OutHeader')
        out_header.dt = datetime(year=2000, month=01, day=01)
        out_header.f = 3.141592653

        ret = self.client.service.send_out_header()

        print ret
        raise Exception("test something!")

    def test_echo_string(self):
        test_string = "OK"
        ret = self.client.service.echo_string(test_string)

        self.assertEquals(ret, test_string)

    def __get_xml_test_val(self):
        return {
            "test_sub": {
                "test_subsub1": {
                    "test_subsubsub1" : ["subsubsub1 value"]
                },
                "test_subsub2": ["subsub2 value 1", "subsub2 value 2"],
                "test_subsub3": [
                    {
                        "test_subsub3sub1": ["subsub3sub1 value"]
                    },
                    {
                        "test_subsub3sub2": ["subsub3sub2 value"]
                    },
                ],
                "test_subsub4": [],
                "test_subsub5": ["x"],
            }
        }

    def test_any(self):
        val=self.__get_xml_test_val()
        ret = self.client.service.echo_any(val)

        self.assertEquals(ret, val)

    def test_any_as_dict(self):
        val=self.__get_xml_test_val()
        ret = self.client.service.echo_any_as_dict(val)

        self.assertEquals(ret, val)

    def test_echo_simple_class(self):
        val = self.client.factory.create("SimpleClass")

        val.i = 45
        val.s = "asd"

        ret = self.client.service.echo_simple_class(val)

        assert ret.i == val.i
        assert ret.s == val.s

    def test_echo_nested_class(self):
        val = self.client.factory.create("{punk.tunk}NestedClass");

        val.i = 45
        val.s = "asd"
        val.f = 12.34
        val.ai = self.client.factory.create("integerArray")
        val.ai.integer.extend([1,2,3,45,5,3,2,1,4])

        val.simple = self.client.factory.create("SimpleClassArray")

        val.simple.SimpleClass.append(self.client.factory.create("SimpleClass"))
        val.simple.SimpleClass.append(self.client.factory.create("SimpleClass"))

        val.simple.SimpleClass[0].i = 45
        val.simple.SimpleClass[0].s = "asd"
        val.simple.SimpleClass[1].i = 12
        val.simple.SimpleClass[1].s = "qwe"

        val.other = self.client.factory.create("OtherClass");
        val.other.dt = datetime.now()
        val.other.d = 123.456
        val.other.b = True

        ret = self.client.service.echo_nested_class(val)
        print ret
        raise Exception("test something! :)")
        # TODO: write asserts

    def test_echo_extension_class(self):
        service_name = "echo_extension_class";
        val = self.client.factory.create("{bar}ExtensionClass");

        val.i = 45
        val.s = "asd"
        val.f = 12.34

        val.simple = self.client.factory.create("SimpleClassArray")

        val.simple.SimpleClass.append(self.client.factory.create("SimpleClass"))
        val.simple.SimpleClass.append(self.client.factory.create("SimpleClass"))

        val.simple.SimpleClass[0].i = 45
        val.simple.SimpleClass[0].s = "asd"
        val.simple.SimpleClass[1].i = 12
        val.simple.SimpleClass[1].s = "qwe"

        val.other = self.client.factory.create("OtherClass");
        val.other.dt = datetime.now()
        val.other.d = 123.456
        val.other.b = True

        val.p = self.client.factory.create("{hunk.sunk}NonNillableClass");
        val.p.dt = datetime(2010,06,02)
        val.p.i = 123
        val.p.s = "punk"

        val.l = datetime(2010,07,02)
        val.q = 5

        ret = self.client.service.echo_extension_class(val)
        print ret
        raise Exception("test something! :)")
        # TODO: write asserts

    def test_python_exception(self):
        try:
            self.client.service.python_exception()
            raise Exception("must fail")
        except WebFault, e:
            pass

    def test_soap_exception(self):
        try:
            self.client.service.soap_exception()
            raise Exception("must fail")
        except WebFault, e:
            pass

if __name__ == '__main__':
    unittest.main()
