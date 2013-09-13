
ncclient: Python library for NETCONF clients
--------------------------------------------

`ncclient` is a Python library that facilitates client-side scripting
and application development around the NETCONF protocol. `ncclient` was
developed by [Shikar Bhushan](http://schmizz.net). It is now maintained
by [Leonidas Poulopoulos](http://ncclient.grnet.gr)

This fork serves for purposes of projects of
[CZ.NIC Labs](https://labs.nic.cz/). Basically, it adds the support of
proprietary stdio transport, when the server communicates with the client
through standard input and output of a subprocess.
It is based on [fork of ncclient](https://github.com/vbajpai/ncclient) by
[Vaibhav Bajpai's](http://vaibhavbajpai.com) that supports NETCONF v1.1
standard as described in [RFC 6242].

Requirements:  
* Python 2.6 <= version < 3.0  
* Paramiko 1.7.7.1+  

Installation:

    [ncclient] $ mkvirtualenv ncclient
    [ncclient] $ make

Usage:

    [ncclient] $ python examples/ncXX.py 
