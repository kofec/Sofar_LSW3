#!/usr/bin/python3
# Script gathering solar data from Sofar Solar Inverter (K-TLX) via logger module LSW-3/LSE
# by Michalux (based on DEYE script by jlopez77)
# Version: 1.61
#

import sys
import socket
import binascii
import re
# import libscrc
import json
import os
import configparser
import logging
from gw2pvo import pvo_api
from gw2pvo import __version__
# import datetime
from datetime import datetime


def twosComplement_hex(hexval, reg):
    if hexval == "":
        print("No value in response for register " + reg)
        print("Check register start/end values in config.cfg")
        sys.exit(1)
    bits = 16
    val = int(hexval, bits)
    if val & (1 << (bits - 1)):
        val -= 1 << bits
    return val


def PrepareDomoticzData(DData, idx, svalue):
    if isinstance(svalue, str):
        DData.append('{ "idx": ' + str(idx) + ', "svalue": ' + svalue + ' }')
    else:
        DData.append('{ "idx": ' + str(idx) + ', "svalue": "' + str(svalue) + '" }')
    return DData


os.chdir(os.path.dirname(sys.argv[0]))

# CONFIG
configParser = configparser.RawConfigParser()
configFilePath = r'./config.cfg'
configParser.read(configFilePath)

inverter_ip = configParser.get('SofarInverter', 'inverter_ip')
inverter_port = int(configParser.get('SofarInverter', 'inverter_port'))
inverter_sn = int(configParser.get('SofarInverter', 'inverter_sn'))
reg_start1 = (int(configParser.get('SofarInverter', 'register_start1'), 0))
reg_end1 = (int(configParser.get('SofarInverter', 'register_end1'), 0))
reg_start2 = (int(configParser.get('SofarInverter', 'register_start2'), 0))
reg_end2 = (int(configParser.get('SofarInverter', 'register_end2'), 0))
lang = configParser.get('SofarInverter', 'lang')
verbose = configParser.get('SofarInverter', 'verbose')
DomoticzSupport = configParser.get('Domoticz', 'domoticz_support')
pvo_system_id = configParser.get('PVOutput', 'pvo_system_id')
pvo_api_key = configParser.get('PVOutput', 'pvo_api_key')
# END CONFIG

timestamp = str(datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'))

# Configure the logging
if verbose == "1":
    numeric_level = getattr(logging, "DEBUG", None)
else:
    numeric_level = getattr(logging, "INFO", None)
if not isinstance(numeric_level, int):
    raise ValueError('Invalid log level: %s' % loglevel)
logging.basicConfig(format='%(levelname)-8s %(message)s', level=numeric_level)
logging.debug("gw2pvo version " + __version__)


# PREPARE & SEND DATA TO THE INVERTER
output = "{"  # initialise json output
pini = reg_start1
pfin = reg_end1
chunks = 0
totalpower = 0
totaltime = 0
PMData = []
DomoticzData = []
CRC_static = ['45d4', '45c3']

while chunks < 2:
    if verbose == "1":
        print("*** Chunk no: ", chunks)

    # Data frame begin
    start = binascii.unhexlify('A5')  # start
    length = binascii.unhexlify('1700')  # datalength
    controlcode = binascii.unhexlify('1045')  # controlCode
    serial = binascii.unhexlify('0000')  # serial
    datafield = binascii.unhexlify(
        '020000000000000000000000000000')  # com.igen.localmode.dy.instruction.send.SendDataField
    # pos_ini=str(hex(pini)[2:4].zfill(2))+str(hex(pini)[4:6].zfill(2))
    pos_ini = str(hex(pini)[2:4].zfill(4))
    pos_fin = str(hex(pfin - pini + 1)[2:4].zfill(4))
    businessfield = binascii.unhexlify('0103' + pos_ini + pos_fin)  # sin CRC16MODBUS
    # print(str(hex(libscrc.modbus(businessfield))[4:6]) + str(hex(libscrc.modbus(businessfield))[2:4]))
    # crc = binascii.unhexlify(
    #    str(hex(libscrc.modbus(businessfield))[4:6]) + str(hex(libscrc.modbus(businessfield))[2:4]))  # CRC16modbus
    crc = binascii.unhexlify(CRC_static[chunks])
    checksum = binascii.unhexlify('00')  # checksum F2
    endCode = binascii.unhexlify('15')

    inverter_sn2 = bytearray.fromhex(
        hex(inverter_sn)[8:10] + hex(inverter_sn)[6:8] + hex(inverter_sn)[4:6] + hex(inverter_sn)[2:4])
    frame = bytearray(
        start + length + controlcode + serial + inverter_sn2 + datafield + businessfield + crc + checksum + endCode)
    if verbose == "1":
        print("Sent data: ", frame)
    # Data frame end

    checksum = 0
    frame_bytes = bytearray(frame)
    for i in range(1, len(frame_bytes) - 2, 1):
        checksum += frame_bytes[i] & 255
    frame_bytes[len(frame_bytes) - 2] = int((checksum & 255))

    # OPEN SOCKET
    for res in socket.getaddrinfo(inverter_ip, inverter_port, socket.AF_INET, socket.SOCK_STREAM):
        family, socktype, proto, canonname, sockadress = res
        try:
            clientSocket = socket.socket(family, socktype, proto)
            clientSocket.settimeout(10)
            clientSocket.connect(sockadress)
        except socket.error as msg:
            print("Could not open socket - inverter/logger turned off")
            sys.exit(1)

    # SEND DATA
    clientSocket.sendall(frame_bytes)

    ok = False
    while not ok:
        try:
            data = clientSocket.recv(1024)
            ok = True
            try:
                data
            except:
                print("No data - Exit")
                sys.exit(1)  # Exit, no data
        except socket.timeout as msg:
            print("Connection timeout - inverter and/or gateway is off")
            sys.exit(1)  # Exit

    # PARSE RESPONSE (start position 56, end position 60)
    if verbose == "1":
        print("Received data: ", data)
    i = pfin - pini
    a = 0
    while a <= i:
        p1 = 56 + (a * 4)
        p2 = 60 + (a * 4)
        hexpos = str("0x") + str(hex(a + pini)[2:].zfill(4)).upper()
        response = twosComplement_hex(
            str(''.join(hex(ord(chr(x)))[2:].zfill(2) for x in bytearray(data)) + '  ' + re.sub('[^\x20-\x7f]', '',
                                                                                                ''))[p1:p2], hexpos)
        with open("./SOFARMap.xml") as txtfile:
            parameters = json.loads(txtfile.read())
        for parameter in parameters:
            for item in parameter["items"]:
                if lang == "PL":
                    title = item["titlePL"]
                else:
                    title = item["titleEN"]
                ratio = item["ratio"]
                unit = item["unit"]
                graph = item["graph"]
                metric_name = item["metric_name"]
                label_name = item["label_name"]
                label_value = item["label_value"]
                metric_type = item["metric_type"]
                DomoticzIdx = item["DomoticzIdx"]
                for register in item["registers"]:
                    if register == hexpos and chunks != -1:
                        response = round(response * ratio, 2)
                        for option in item["optionRanges"]:
                            if option["key"] == response:
                                if label_name == "Status":
                                    if response == 2:
                                        invstatus = 1
                                    else:
                                        invstatus = 0
                                if lang == "PL":
                                    response = '"' + option["valuePL"] + '"'
                                else:
                                    response = '"' + option["valueEN"] + '"'
                        if hexpos != '0x0015' and hexpos != '0x0016' and hexpos != '0x0017' and hexpos != '0x0018':
                            if verbose == "1":
                                print(hexpos + " - " + title + ": " + str(response) + unit)
                            if DomoticzSupport == "1" and DomoticzIdx > 0:
                                PrepareDomoticzData(DomoticzData, DomoticzIdx, response)
                            if unit != "":
                                output = output + "\"" + title + " (" + unit + ")" + "\":" + str(response) + ","
                            else:
                                output = output + "\"" + title + "\":" + str(response) + ","
                        if hexpos == '0x0015':
                            totalpower += response * ratio * 65536
                        if hexpos == '0x0016':
                            totalpower += response * ratio
                            if verbose == "1":
                                print(hexpos + " - " + title + ": " + str(response * ratio) + unit)
                            output = output + "\"" + title + " (" + unit + ")" + "\":" + str(totalpower) + ","
                            if DomoticzSupport == "1" and DomoticzIdx > 0:
                                PrepareDomoticzData(DomoticzData, DomoticzIdx, response)
                        if hexpos == '0x0017':
                            totaltime += response * ratio * 65536
                        if hexpos == '0x0018':
                            totaltime += response * ratio
                            if verbose == "1":
                                print(hexpos + " - " + title + ": " + str(response * ratio) + unit)
                            output = output + "\"" + title + " (" + unit + ")" + "\":" + str(totaltime) + ","
                            if DomoticzSupport == "1" and DomoticzIdx > 0:
                                PrepareDomoticzData(DomoticzData, DomoticzIdx, response)
        a += 1
    if chunks == 0:
        pini = reg_start2
        pfin = reg_end2
    chunks += 1
output = output[:-1] + "}"

# Domoticz integration
# Send data to Domoticz if support enabled
if DomoticzSupport == "1":
    if verbose == "1":
        print("*** Messages for Domoticz:")
    print("Publishing data for Domoticz")

print("*** JSON output:")
jsonoutput = json.loads(output)
print(json.dumps(jsonoutput, indent=4, sort_keys=False, ensure_ascii=False))

if pvo_system_id and pvo_api_key:
    pgrid_w = jsonoutput["Output active power (W)"]
    eday_kwh = int(jsonoutput["Today production (Wh)"])/1000
    voltage = jsonoutput["L1 Voltage (V)"]
    pvo = pvo_api.PVOutputApi(pvo_system_id, pvo_api_key)
    pvo.add_status(pgrid_w=pgrid_w, eday_kwh=eday_kwh, temperature=None, voltage=voltage)
else:
    print(output)
    print(jsonoutput)
    print("Missing PVO id and/or key")

