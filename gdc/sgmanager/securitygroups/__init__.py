# -*- coding: utf-8 -*-
# Copyright (C) 2007-2013, GoodData(R) Corporation. All rights reserved

import os
import yaml
import logging

import gdc.sgmanager
from gdc.sgmanager.decorators import CachedMethod
from gdc.sgmanager.exceptions import InvalidConfiguration

from gdc.sgmanager.securitygroups.sgroup import SGroup
from gdc.sgmanager.securitygroups.srule import SRule

lg = logging.getLogger(__name__)


class SecurityGroups(object):
    def __init__(self):
        """
        Create instance, save ec2 connection
        """
        global ec2
        ec2 = gdc.sgmanager.ec2

        self.groups = {}
        self.config = None

    def load_remote_groups(self):
        """
        Load security groups from EC2
        Convert boto.ec2.securitygroup objects into unified structure
        :rtype : list
        """
        lg.debug("Loading remote groups")
        groups = ec2.get_all_security_groups()
        for group in groups:
            # Initialize SGroup object
            sgroup = SGroup(str(group.name), str(group.description), sgroup_object=group)

            # For each rule in group
            for rule in group.rules:
                rule_info = {
                    'protocol'  : str(rule.ip_protocol),
                    'srule_object' : rule,
                }

                if rule.from_port != rule.to_port:
                    # We have port range, use port_from and port_to parameters
                    rule_info['port_from'] = int(rule.from_port)
                    rule_info['port_to'] = int(rule.to_port)
                else:
                    # We have single port, use port parameter
                    rule_info['port'] = int(rule.to_port)

                if rule.grants:
                    for grant in rule.grants:
                        # For each granted permission, add new SRule
                        try:
                            srule = SRule(groups={
                                'name'  : str(grant.groupName),
                                'owner' : str(grant.owner_id),
                                'id'    : str(grant.groupId)
                            }, **rule_info)
                        except AttributeError:
                            srule = SRule(cidr=[ str(grant.cidr_ip) ], **rule_info)

                        sgroup.add_rule(srule)

            self.groups[sgroup.name] = sgroup

        return self.groups

    def load_local_groups(self, config):
        """
        Load local groups from config file
        Save and return SecurityGroups object

        :param config:
        :rtype : object
        """
        self.config = config
        yaml.add_constructor('!include', self._yaml_include)

        try:
            with open(config, 'r') as fp:
                conf = yaml.load(fp)
        except IOError as e:
            # Error while loading file
            raise InvalidConfiguration("Can't read config file %s: %s" % (config, e))
        except yaml.YAMLError as e:
            # Error while parsing YAML
            if hasattr(e, 'problem_mark'):
                mark = e.problem_mark
                raise InvalidConfiguration("Can't parse config file %s: error at line %s, column %s" % (config, mark.line+1, mark.column+1))
            else:
                raise InvalidConfiguration("Can't parse config file %s: %s" % (config, e))

        lg.debug("Loading local groups")
        for name, group in conf.iteritems():
            # Initialize SGroup object
            sgroup = SGroup(name, None if not group.has_key('description') else group['description'])

            if group.has_key('rules'):
                for rule in group['rules']:
                    if rule.has_key('groups'):
                        # For each group, create separate rule
                        # multiple groups are used only to simplify configuration
                        for group in rule['groups']:
                            rule['groups'] = [ group ]
                            srule = SRule(**rule)
                            sgroup.add_rule(srule)
                    else:
                        # No groups, initialize SRule object
                        srule = SRule(**rule)
                        # Add it into group
                        sgroup.add_rule(srule)

            self.groups[name] = sgroup

        return self.groups

    def _yaml_include(self, loader, node):
        """
        Include another yaml file from main file
        This is usually done by registering !include tag
        """
        filepath = "%s/%s" % (os.path.dirname(self.config), node.value)
        try:
            with open(filepath, 'r') as inputfile:
                return yaml.load(inputfile)
        except IOError as e:
            raise InvalidConfiguration("Can't include config file %s: %s" % (filepath, e))

    def dump_groups(self):
        """
        Return YAML dump of loaded groups
        :rtype : basestring
        """
        return yaml.dump({ name : group.dump() for name, group in self.groups.iteritems() }, Dumper=YamlDumper)

    def has_group(self, name):
        if self.groups.has_key(name):
            return True
        else:
            return False

    def __eq__(self, other):
        """
        Equal matching, just call self.compare and return boolean
        """
        added, removed, updated, unchanged = self.compare(other)
        if added or removed or updated:
            return False
        else:
            return True

    def __ne__(self, other):
        """
        Not equal matching, call __eq__ but reverse output
        """
        return not self.__eq__(other)

    @CachedMethod
    def compare(self, other):
        """
        Compare SecurityGroups objects

        Return tuple of lists with SGroups objects (added, removed, updated, unchanged)
        """
        if not isinstance(other, SecurityGroups):
            raise TypeError("Compared object must be instance of SecurityGroups, not %s" % type(other).__name__)

        added = []
        removed = []
        updated = []
        unchanged = []

        for name, group in self.groups.iteritems():
            # Group doesn't exist in target SecurityGroups object
            if not other.has_group(name):
                added.append(group)
                continue

            # Compare matched group
            if group != other.groups[name]:
                # Differs - update
                updated.append(group)
            else:
                # Group is same as other one - unchanged
                unchanged.append(group)

        # Check if some groups shouldn't be removed
        for name, group in other.groups.iteritems():
            if not self.has_group(name):
                removed.append(group)

        return added, removed, updated, unchanged


class YamlDumper(yaml.SafeDumper):
    """
    Custom YAML dumper that will ignore aliases
    """
    def ignore_aliases(self, _data):
        return True