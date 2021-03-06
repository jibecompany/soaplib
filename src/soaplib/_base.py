
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

import logging
logger = logging.getLogger("soaplib._base")

import shutil
import tempfile
import traceback

from lxml import etree

import soaplib

from soaplib.serializers.exception import Fault
from soaplib.serializers.primitive import string_encoding
from soaplib.util.odict import odict

from soaplib.soap import from_soap

HTTP_500 = '500 Internal server error'
HTTP_200 = '200 OK'
HTTP_405 = '405 Method Not Allowed'

class ValidationError(Fault):
    pass

class _SchemaInfo(object):
    def __init__(self):
        self.elements = odict()
        self.types = odict()

class _SchemaEntries(object):
    def __init__(self, tns, app):
        self.namespaces = odict()
        self.imports = {}
        self.tns = tns
        self.app = app

    def has_class(self, cls):
        retval = False
        ns_prefix = cls.get_namespace_prefix(self.app)

        if ns_prefix in soaplib.const_nsmap:
            retval = True

        else:
            type_name = cls.get_type_name()

            if (ns_prefix in self.namespaces) and \
                              (type_name in self.namespaces[ns_prefix].types):
                retval = True

        return retval

    def get_schema_info(self, prefix):
        if prefix in self.namespaces:
            schema = self.namespaces[prefix]
        else:
            schema = self.namespaces[prefix] = _SchemaInfo()

        return schema

    # FIXME: this is an ugly hack. we need proper dependency management
    def __check_imports(self, cls, node):
        pref_tns = cls.get_namespace_prefix(self.app)

        def is_valid_import(pref):
            return not (
                (pref in soaplib.const_nsmap) or (pref == pref_tns)
            )

        if not (pref_tns in self.imports):
            self.imports[pref_tns] = set()

        for c in node:
            if c.tag == "{%s}complexContent" % soaplib.ns_xsd:
                seq = c.getchildren()[0].getchildren()[0] # FIXME: ugly, isn't it?

                extension = c.getchildren()[0]
                if extension.tag == '{%s}extension' % soaplib.ns_xsd:
                    pref = extension.attrib['base'].split(':')[0]
                    if is_valid_import(pref):
                        self.imports[pref_tns].add(self.app.nsmap[pref])
            else:
                seq = c

            if seq.tag == '{%s}sequence' % soaplib.ns_xsd:
                for e in seq:
                    pref = e.attrib['type'].split(':')[0]
                    if is_valid_import(pref):
                        self.imports[pref_tns].add(self.app.nsmap[pref])

            elif seq.tag == '{%s}restriction' % soaplib.ns_xsd:
                pref = seq.attrib['base'].split(':')[0]
                if is_valid_import(pref):
                    self.imports[pref_tns].add(self.app.nsmap[pref])

            else:
                raise Exception("i guess you need to hack some more")

    def add_element(self, cls, node):
        schema_info = self.get_schema_info(cls.get_namespace_prefix(self.app))
        schema_info.elements[cls.get_type_name()] = node

    def add_simple_type(self, cls, node):
        self.__check_imports(cls, node)
        schema_info = self.get_schema_info(cls.get_namespace_prefix(self.app))
        schema_info.types[cls.get_type_name()] = node

    def add_complex_type(self, cls, node):
        self.__check_imports(cls, node)
        schema_info = self.get_schema_info(cls.get_namespace_prefix(self.app))
        schema_info.types[cls.get_type_name()] = node

class Application(object):
    transport = None

    def __init__(self, services, tns, name=None, _with_partnerlink=False):
        '''
        @param A ServiceBase subclass that defines the exposed services.
        '''

        self.services = services
        self.__tns = tns
        self.__name = name
        self._with_plink = _with_partnerlink

        self.call_routes = {}
        self.__wsdl = None
        self.__public_methods = {}
        self.schema = None

        self.__ns_counter = 0

        self.nsmap = dict(soaplib.const_nsmap)
        self.prefmap = dict(soaplib.const_prefmap)

        self.build_schema()

    def __decompose_request(self, envelope_string, charset=None):
        service_ctx = None
        method_name = None

        # deserialize the body of the message
        header, body = from_soap(envelope_string, charset)

        if not (body is None):
            try:
                self.validate_request(body)

            finally:
                # for performance reasons, we don't want the following to run
                # in production even though we won't see the results.
                if logger.level == logging.DEBUG:
                    try:
                        logger.debug(etree.tostring(body, pretty_print=True))
                    except etree.XMLSyntaxError,e:
                        logger.debug(body)
                        raise Fault('Client.XMLSyntax', 'Error at line: %d, '
                                    'col: %d' % e.position)

            if not (body is None):
                method_name = body.tag
                logger.debug("\033[92mMethod name: %r\033[0m" % method_name)

        if method_name is None:
            raise Exception("Could not extract method name from the request!")

        service_class = self.get_service_class(method_name)
        service_ctx = self.get_service(service_class)

        service_ctx.header_xml = header
        service_ctx.body_xml = body
        service_ctx.method_name = method_name

        return service_ctx

    def deserialize_soap(self, envelope_string, charset=None):
        """Takes a string containing ONE soap message.
        Returns the corresponding native python object, along with the request
        context

        Not meant to be overridden.
        """

        try:
            ctx = self.__decompose_request(envelope_string, charset)
        except ValidationError, e:
            return None, e

        # retrieve the method descriptor
        descriptor = ctx.descriptor = ctx.get_method(ctx.method_name)

        # decode header object
        if ctx.header_xml is not None and len(ctx.header_xml) > 0:
            in_header = descriptor.in_header
            ctx.soap_in_header = in_header.from_xml(ctx.header_xml)

        # decode method arguments
        if ctx.body_xml is not None and len(ctx.body_xml) > 0:
            params = descriptor.in_message.from_xml(ctx.body_xml)
        else:
            params = [None] * len(descriptor.in_message._type_info)

        return ctx, params

    def process_request(self,ctx,req_obj):
        """Takes the native request object.
        Returns the response to the request as a native python object.

        Not meant to be overridden.
        """

        try:
            # retrieve the method
            func = getattr(ctx, ctx.descriptor.name)

            # call the method
            return ctx.call_wrapper(func, req_obj)

        except Fault, e:
            return e

        except Exception, e:
            fault = Fault('Server', str(e))

            return fault

    def serialize_soap(self, ctx, native_obj):
        """Pushes the native python object to the output stream as a soap
        response

        Not meant to be overridden.
        """

        # construct the soap response, and serialize it
        envelope = etree.Element('{%s}Envelope' % soaplib.ns_soap_env,
                                                               nsmap=self.nsmap)

        if ctx is None or isinstance(native_obj, Exception):
            stacktrace=traceback.format_exc()
            logger.error(stacktrace)

            # implementation hook
            if not (ctx is None):
                ctx.on_method_exception_object(native_obj)
            self.on_exception_object(native_obj)

            # FIXME: There's no way to alter soap response headers for the user.
            soap_body = etree.SubElement(envelope,
                            '{%s}Body' % soaplib.ns_soap_env, nsmap=self.nsmap)
            native_obj.__class__.to_xml(native_obj, self.get_tns(), soap_body)

            # implementation hook
            if not (ctx is None):
                ctx.on_method_exception_xml(soap_body)
                ctx.soap_body = soap_body
            self.on_exception_xml(soap_body)

            if logger.level == logging.DEBUG:
                logger.debug(etree.tostring(envelope, pretty_print=True))

        else:
            #
            # header
            #
            if ctx.soap_out_header != None:
                if ctx.descriptor.out_header is None:
                    logger.warning(
                        "Skipping soap response header as %r method is not "
                        "published to have a soap response header" %
                                native_obj.get_type_name()[:-len('Response')])
                else:
                    soap_header_elt = etree.SubElement(envelope,
                                             '{%s}Header' % soaplib.ns_soap_env)
                    ctx.descriptor.out_header.to_xml(
                        ctx.soap_out_header,
                        self.get_tns(),
                        soap_header_elt,
                        ctx.descriptor.out_header.get_type_name()
                    )

            #
            # body
            #
            ctx.soap_body = soap_body = etree.SubElement(envelope,
                                               '{%s}Body' % soaplib.ns_soap_env)

            # instantiate the result message
            result_message = ctx.descriptor.out_message()

            # assign raw result to its wrapper, result_message
            out_type = ctx.descriptor.out_message._type_info

            if len(out_type) > 0:
                assert len(out_type) == 1

                attr_name = ctx.descriptor.out_message._type_info.keys()[0]
                setattr(result_message, attr_name, native_obj)

            # transform the results into an element
            ctx.descriptor.out_message.to_xml(
                                      result_message, self.get_tns(), soap_body)

            if logger.level == logging.DEBUG:
                logger.debug('\033[91m'+ "Response" + '\033[0m')
                logger.debug(etree.tostring(envelope, xml_declaration=True,
                                                             pretty_print=True))

        return etree.tostring(envelope, xml_declaration=True,
                                                       encoding=string_encoding)

    def get_namespace_prefix(self, ns):
        """Returns the namespace prefix for the given namespace. Creates a new
        one automatically if it doesn't exist

        Not meant to be overridden.
        """

        assert ns != "__main__"
        assert ns != "soaplib.serializers.base"

        assert (isinstance(ns, str) or isinstance(ns, unicode)), ns

        if not (ns in self.prefmap):
            pref = "s%d" % self.__ns_counter
            while pref in self.nsmap:
                self.__ns_counter += 1
                pref = "s%d" % self.__ns_counter

            self.prefmap[ns] = pref
            self.nsmap[pref] = ns

            self.__ns_counter += 1

        else:
            pref = self.prefmap[ns]

        return pref

    def set_namespace_prefix(self, ns, pref):
        """Forces a namespace prefix on a namespace by either creating it or
        moving the existing namespace to a new prefix

        Not meant to be overridden.
        """

        if pref in self.nsmap and self.nsmap[pref] != ns:
            ns_old = self.nsmap[pref]
            del self.prefmap[ns_old]
            self.get_namespace_prefix(ns_old)

        cpref = self.get_namespace_prefix(ns)
        del self.nsmap[cpref]

        self.prefmap[ns] = pref
        self.nsmap[pref] = ns

    def get_name(self):
        """Returns service name that is seen in the name attribute of the
        definitions tag.

        Not meant to be overridden.
        """
        retval = self.__name

        if retval is None:
            retval = self.__class__.__name__.split('.')[-1]

        return retval

    name = property(get_name)

    def get_tns(self):
        """Returns default namespace that is seen in the targetNamespace 
        attribute of the definitions tag.

        Not meant to be overridden.
        """
        retval = self.__tns

        if retval is None:
            service_name = self.get_name()

            if self.__class__.__module__ == '__main__':
                retval = '.'.join((service_name, service_name))
            else:
                retval = '.'.join((self.__class__.__module__, service_name))

            if retval.startswith('soaplib'):
                retval = self.services[0].get_tns()

        return retval

    tns = property(get_tns)

    def __build_schema_nodes(self, schema_entries, types=None):
        """Fill individual <schema> nodes for every service that are part of this
        app.
        """

        schema_nodes = {}

        for pref in schema_entries.namespaces:
            schema = self.__get_schema_node(pref, schema_nodes, types)

            # append import tags
            for namespace in schema_entries.imports[pref]:
                import_ = etree.SubElement(schema, "{%s}import"% soaplib.ns_xsd)
                import_.set("namespace", namespace)
                if types is None:
                    import_.set('schemaLocation', "%s.xsd" %
                                        self.get_namespace_prefix(namespace))

            # append element tags
            for node in schema_entries.namespaces[pref].elements.values():
                schema.append(node)

            # append simpleType and complexType tags
            for node in schema_entries.namespaces[pref].types.values():
                schema.append(node)

        return schema_nodes

    def build_schema(self, types=None):
        """Unify the <schema> nodes required for this app.

        This is a protected method.
        """

        if types is None:
            # populate call routes
            for s in self.services:
                s.__tns__ = self.get_tns()
                inst = self.get_service(s)

                for method in inst.public_methods:
                    method_name = "{%s}%s" % (self.get_tns(), method.name)

                    if method_name in self.call_routes:
                        o = self.call_routes[method_name]
                        raise Exception("%s.%s.%s overwrites %s.%s.%s" %
                                        (s.__module__, s.__name__, method.name,
                                         o.__module__, o.__name__, method.name))

                    else:
                        logger.debug('adding method %r' % method_name)
                        self.call_routes[method_name] = s
                        self.call_routes[method.name] = s

        # populate types
        schema_entries = _SchemaEntries(self.get_tns(), self)
        for s in self.services:
            inst = self.get_service(s)
            schema_entries = inst.add_schema(schema_entries)

        schema_nodes = self.__build_schema_nodes(schema_entries, types)

        return schema_nodes

    def get_service_class(self, method_name):
        """This call maps method names to the services that will handle them.

        Override this function to alter the method mappings. Just try not to get
        too crazy with regular expressions :)
        """
        return self.call_routes[method_name]

    def get_service(self, service, http_req_env=None):
        """The function that maps service classes to service instances.
        Overriding this function is useful in case e.g. you need to pass
        additional parameters to service constructors.
        """
        return service(http_req_env)

    def get_schema(self):
        """Simple accessor method that caches application's xml schema, once
        generated.

        Not meant to be overridden.
        """
        if self.schema is None:
            return self.build_schema()
        else:
            return self.schema

    def get_wsdl(self, url):
        """Simple accessor method that caches the wsdl of the application, once
        generated.

        Not meant to be overridden.
        """
        if self.__wsdl is None:
            return self.__build_wsdl(url)
        else:
            return self.__wsdl

    def __get_schema_node(self, pref, schema_nodes, types):
        """Return schema node for the given namespace prefix.

        types == None means the call is for creating a standalone xml schema
                      file for one single namespace.
        types != None means the call is for creating the wsdl file.
        """

        # create schema node
        if not (pref in schema_nodes):
            if types is None:
                schema = etree.Element("{%s}schema" % soaplib.ns_xsd,
                                                        nsmap=self.nsmap)
            else:
                schema = etree.SubElement(types, "{%s}schema" % soaplib.ns_xsd)

            schema.set("targetNamespace", self.nsmap[pref])
            schema.set("elementFormDefault", "qualified")

            schema_nodes[pref] = schema

        else:
            schema = schema_nodes[pref]

        return schema

    def __build_wsdl(self, url):
        """Build the wsdl for the application.
        """
        ns_wsdl = soaplib.ns_wsdl
        ns_soap = soaplib.ns_soap
        ns_plink = soaplib.ns_plink

        ns_tns = self.get_tns()
        pref_tns = 'tns'
        self.set_namespace_prefix(ns_tns, pref_tns)

        # FIXME: doesn't look so robust
        url = url.replace('.wsdl', '')

        service_name = self.get_name()

        # create wsdl root node
        root = etree.Element("{%s}definitions" % ns_wsdl, nsmap=self.nsmap)
        root.set('targetNamespace', ns_tns)
        root.set('name', service_name)

        # create types node
        types = etree.SubElement(root, "{%s}types" % ns_wsdl)

        self.build_schema(types)
        messages = set()

        for s in self.services:
            s=self.get_service(s,None)

            s.add_messages_for_methods(self, root, messages)

        if self._with_plink:
            # create plink node
            plink = etree.SubElement(root, '{%s}partnerLinkType' % ns_plink)
            plink.set('name', service_name)
            self.__add_partner_link(root, service_name, types, url, plink)

        # create service node
        service = etree.SubElement(root, '{%s}service' % ns_wsdl)
        service.set('name', service_name)
        self.__add_service(root, service_name, types, url, service)

        # create portType node
        port_type = etree.SubElement(root, '{%s}portType' % ns_wsdl)
        port_type.set('name', service_name)

        # create binding nodes
        binding = etree.SubElement(root, '{%s}binding' % ns_wsdl)
        binding.set('name', service_name)
        binding.set('type', '%s:%s'% (pref_tns, service_name))

        soap_binding = etree.SubElement(binding, '{%s}binding' % ns_soap)
        soap_binding.set('style', 'document')

        if self.transport is None:
            raise Exception("You must set the class variable 'transport'")
        soap_binding.set('transport', self.transport)

        cb_binding = None

        for s in self.services:
            s=self.get_service(s)
            s.add_port_type(self, root, service_name, types, url, port_type)
            cb_binding = s.add_bindings_for_methods(self, root, service_name,
                                                types, url, binding, cb_binding)

        self.__wsdl = etree.tostring(root, xml_declaration=True,
                                                               encoding="UTF-8")

        return self.__wsdl

    def __add_partner_link(self, root, service_name, types, url, plink):
        """Add the partnerLinkType node to the wsdl.
        """
        ns_plink = soaplib.ns_plink
        ns_tns = self.get_tns()
        pref_tns = self.get_namespace_prefix(ns_tns)

        role = etree.SubElement(plink, '{%s}role' % ns_plink)
        role.set('name', service_name)

        plink_port_type = etree.SubElement(role, '{%s}portType' % ns_plink)
        plink_port_type.set('name', '%s:%s' % (pref_tns, service_name))

        if self._has_callbacks():
            role = etree.SubElement(plink, '{%s}role' % ns_plink)
            role.set('name', '%sCallback' % service_name)

            plink_port_type = etree.SubElement(role, '{%s}portType' % ns_plink)
            plink_port_type.set('name', '%s:%sCallback' %
                                                       (pref_tns, service_name))
    def __add_service(self, root, service_name, types, url, service):
        """Add service node to the wsdl.
        """
        pref_tns = self.get_namespace_prefix(self.get_tns())

        wsdl_port = etree.SubElement(service, '{%s}port' % soaplib.ns_wsdl)
        wsdl_port.set('name', service_name)
        wsdl_port.set('binding', '%s:%s' % (pref_tns, service_name))

        addr = etree.SubElement(wsdl_port, '{%s}address' % soaplib.ns_soap)
        addr.set('location', url)

    def _has_callbacks(self):
        retval = False

        for s in self.services:
            if self.get_service(s)._has_callbacks():
                return True

        return retval

    def validate_request(self, payload):
        """Method to be overriden to perform any sort of custom input
        validation.
        """

    def on_exception_object(self, exc):
        '''Called when the app throws an exception. (might be inside or outside
        the service call.

        @param the wsgi environment
        @param the fault object
        '''

    def on_exception_xml(self, fault_xml):
        '''Called when the app throws an exception. (might be inside or outside
        the service call.

        @param the wsgi environment
        @param the xml element containing the xml serialization of the fault
        '''

class ValidatingApplication(Application):
    def build_schema(self, types=None):
        """Build application schema specifically for xml validation purposes.
        """
        schema_nodes = Application.build_schema(self, types)

        if types is None:
            pref_tns = self.get_namespace_prefix(self.get_tns())
            logger.debug("generating schema for targetNamespace=%r, prefix: %r"
                                                   % (self.get_tns(), pref_tns))

            tmp_dir_name = tempfile.mkdtemp()

            # serialize nodes to files
            for k,v in schema_nodes.items():
                file_name = '%s/%s.xsd' % (tmp_dir_name, k)
                f = open(file_name, 'w')
                etree.ElementTree(v).write(f, pretty_print=True)
                f.close()
                logger.debug("writing %r for ns %s" % (file_name,
                                                            self.nsmap[k]))

            f = open('%s/%s.xsd' % (tmp_dir_name, pref_tns), 'r')

            logger.debug("building schema...")
            self.schema = etree.XMLSchema(etree.parse(f))

            logger.debug("schema %r built, cleaning up..." % self.schema)
            f.close()
            shutil.rmtree(tmp_dir_name)
            logger.debug("removed %r" % tmp_dir_name)

        return self.schema

    def validate_request(self, payload):
        schema = self.schema
        ret = schema.validate(payload)

        logger.debug("validation result: %s" % str(ret))
        if ret == False:
            err = schema.error_log.last_error

            fault_code = 'Client.SchemaValidation'

            raise ValidationError(fault_code, faultstring=str(err))
