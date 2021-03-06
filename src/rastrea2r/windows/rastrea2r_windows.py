#!/usr/bin/env python
#
# rastrea2r windows client




import os

# import winreg
import glob
import hashlib
import platform
import psutil  # New multiplatform library
import shutil
import subprocess
import sys
import yara
import zipfile
from argparse import ArgumentParser
from mimetypes import MimeTypes
from requests import post
from time import gmtime, strftime
from requests.auth import HTTPBasicAuth
import json
import logging
import traceback

from utils.http_utils import http_get_request, http_post_request
from rastrea2r import ENABLE_TRACE, AUTH_USER, AUTH_PASSWD, SERVER_PORT, CLIENT_VERSION, API_VERSION, WINDOWS_COMMANDS

__version__ = CLIENT_VERSION

""" Variables """
logger = logging.getLogger(__name__)
BLOCKSIZE = 65536
mime = MimeTypes()


def hashfile(file):
    """ Hashes output files with SHA256 using buffers to reduce memory impact """

    hasher = hashlib.sha256()

    with open(file, 'rb') as afile:
        buf = afile.read(BLOCKSIZE)
        hasher.update(buf)

    return (hasher.hexdigest())


def yaradisk(path, server, rule, silent):
    """ Yara file/directory object scan module """

    results = []
    rule_url = server + ":" + SERVER_PORT + API_VERSION + "/rule?rulename=" + rule
    logger.debug("Rule_URL:"+rule_url)
    rule_text = http_get_request(url=rule_url, auth=HTTPBasicAuth(AUTH_USER, AUTH_PASSWD))

    if not silent:
        logger.debug('\nPulling ' + rule + ' from ' + server + '\n')
        #logger.info(str(rule_text) + '\n')
        logger.debug('\nScanning ' + path + '\n')

    rule_bin = yara.compile(sources={'namespace': rule_text})

    for root, dirs, filenames in os.walk(path):
        for name in filenames:
            try:
                file_path = os.path.join(root, name)

                mime_type = mime.guess_type(file_path)
                if "openxmlformats-officedocument" in mime_type[
                        0]:  # If an OpenXML Office document (docx/xlsx/pptx,etc.)
                    doc = zipfile.ZipFile(file_path)  # Unzip and scan in memory only
                    for doclist in doc.namelist():
                        matches = rule_bin.match(data=doc.read(doclist))
                        if matches:
                            break
                else:
                    matches = rule_bin.match(filepath=file_path)

                if matches:
                    result = {"rulename": str(matches[0]),
                              "filename": file_path,
                              "module": 'yaradisk',
                              "hostname": os.environ['COMPUTERNAME']}
                    if not silent:
                        logger.debug(result)

                    results.append(result)

            except Exception as e:
                logging.error(
                    "Exception when executing yara-disk ERROR: {error}, TRACE: {stack_trace}".format(
                        error=str(e), stack_trace=traceback.format_exc() if ENABLE_TRACE else ""))

    if len(results) > 0:
        logger.debug("Results is: " + str(results))
        headers = {'module': 'yara-disk-scan',
                   'Content-Type': 'application/json'}
        results_url = server + ":" + SERVER_PORT + API_VERSION + '/results'
        response = http_post_request(url=results_url, body=json.dumps(results),
                                                auth=HTTPBasicAuth(AUTH_USER, AUTH_PASSWD),
                                                headers=headers)

        if response.status_code == 200:
            logger.info("yara-disk Results pushed to server successfully")
        else:
            logger.error("Error uploading the results: " + response.text)

    else:
        logger.info("No matches found!!!")


def yaramem(server, rule, silent):
    """ Yara process memory scan module """

    results = []
    rule_url = server + ":" + SERVER_PORT + API_VERSION + "/rule?rulename=" + rule
    rule_text = http_get_request(url=rule_url, auth=HTTPBasicAuth(AUTH_USER, AUTH_PASSWD))

    if not silent:
        logger.debug('\nPulling ' + rule + ' from ' + server + '\n')
        #logger.info(rule_text + '\n')
        logger.debug('\nScanning running processes in memory\n')

    mypid = os.getpid()

    rule_bin = yara.compile(source=rule_text)

    for process in psutil.process_iter():
        try:
            pinfo = process.as_dict(attrs=['pid', 'name', 'exe', 'cmdline'])
        except psutil.NoSuchProcess:
            pass
        else:
            if not silent:
                print(pinfo)

        client_pid = pinfo['pid']
        client_pname = pinfo['name']
        client_ppath = pinfo['exe']
        client_pcmd = pinfo['cmdline']

        if client_pid != mypid:
            try:
                matches = rule_bin.match(pid=client_pid)
            except:
                if not silent:
                    logger.debug('Failed scanning process ID: %d' % client_pid)
                continue

            if matches:
                result = {"rulename": str(matches),
                          "processpath": client_ppath,
                          "processpid": client_pid,
                          "module": 'yaramem',
                          "hostname": os.environ['COMPUTERNAME']}
                if not silent:
                    logger.debug(result)

                results.append(result)

    if len(results) > 0:
        headers = {'module': 'yara-mem-scan',
                   'Content-Type': 'application/json'}
        results_url = server + ":" + SERVER_PORT + API_VERSION + '/results'
        response = http_post_request(url=results_url, body=json.dumps(results),
                                                auth=HTTPBasicAuth(AUTH_USER, AUTH_PASSWD),
                                                headers=headers)

        if response.status_code == 200:
            logger.info("yara-mem Results pushed to server successfully")
        else:
            logger.error("Error uploading the results: " + response.text)

    else:
        logger.info("No matches found!!!")


def memdump(tool_server, output_server, silent):
    """ Memory acquisition module """

    smb_bin = tool_server + r'\tools'  # TOOLS Read-only share with third-party binary tools

    smb_data = output_server + r'\data' + r'\memdump-' + os.environ[
        'COMPUTERNAME']  # DATA Write-only share for output data
    if not os.path.exists(r'\\' + smb_data):
        os.makedirs(r'\\' + smb_data)

    if not silent:
        print('\nSaving output to ' + r'\\' + smb_data)

    tool = ('winpmem -')  # Sends output to STDOUT

    fullcommand = tool.split()
    commandname = fullcommand[0].split('.')

    recivedt = strftime('%Y%m%d%H%M%S', gmtime())  # Timestamp in GMT

    f = open(r'\\' + smb_data + r'\\' + recivedt + '-' + os.environ['COMPUTERNAME'] + '-' + commandname[0] + '.img',
             'w')

    if not silent:
        print('\nDumping memory to ' + r'\\' + smb_data + r'\\' + recivedt + '-' + os.environ['COMPUTERNAME'] + '-'
              + commandname[0] + '.img\n')

    pst = subprocess.call(r'\\' + smb_bin + r'\\' + tool, stdout=f)

    with open(r'\\' + smb_data + r'\\' + recivedt + '-' + os.environ['COMPUTERNAME'] + '-' + 'sha256-hashing.log',
              'a') as g:
        g.write("%s - %s \n\n" % (f.name, hashfile(f.name)))


def triage(tool_server, output_server, silent):
    """ Triage collection module """

    createt = strftime('%Y%m%d%H%M%S', gmtime())  # Timestamp in GMT
    smb_bin = tool_server + r'\tools'  # TOOLS Read-only share with third-party binary tools

    smb_data = output_server + r'\data' + r'\triage-' + os.environ[
        'COMPUTERNAME'] + r'\\' + createt  # DATA Write-only share for output data
    if not os.path.exists(r'\\' + smb_data):
        os.makedirs(r'\\' + smb_data)

    if not silent:
        logger.debug('\nSaving output to ' + r'\\' + smb_data)

    with open(r'\\' + smb_data + r'\\' + createt + '-' + os.environ['COMPUTERNAME'] + '-' + 'sha256-hashing.log',
              'a') as g:
        for task in WINDOWS_COMMANDS:  # Iterates over the list of commands

            fullcommand = str.replace(task, '\n', '')
            commandname = fullcommand.split('.')
            logger.debug("Command to be executed: " + fullcommand)
            
            if not silent:
                logger.debug('\nSaving output of ' + task + ' to ' + r'\\' + smb_data + r'\\' + createt + '-' + os.environ[
                    'COMPUTERNAME']
                    + '-' + commandname[0] + '.log\n')

            f = open(
                r'\\' + smb_data + r'\\' + createt + '-' + os.environ['COMPUTERNAME'] + '-' + commandname[0] + '.log',
                'w')

            pst = subprocess.call(r'\\' + smb_bin + r'\\' + fullcommand, stdout=f)

            g.write("%s - %s \n\n" % (f.name, hashfile(f.name)))

 
def webhist(tool_server, output_server, histuser, silent):
    """ Web History collection module """

    createt = strftime('%Y%m%d%H%M%S', gmtime())  # Timestamp in GMT
    smb_bin = tool_server + r'\tools'  # TOOLS Read-only share with third-party binary tools

    # Setup startupinfo to hide console window when executing via subprocess.call
    si = subprocess.STARTUPINFO()
    si.dwFlags = subprocess.CREATE_NEW_CONSOLE | subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE

    smb_data = output_server + r'\data' + r'\webhistory-' + os.environ[
        'COMPUTERNAME'] + '\\' + createt  # DATA Write-only share for output data
    if not os.path.exists(r'\\' + smb_data):
        os.makedirs(r'\\' + smb_data)

    if not silent:
        print('\nSaving output to ' + smb_data)

    if histuser == 'all':
        user_dirs = next(os.walk('c:\\users\\'))[1]
    else:
        user_dirs = [histuser]

    for user_dir in user_dirs:
        # browserhistoryview.exe command line
        bhv_command = '\\\\' + smb_bin + '\\browsinghistoryview\\browsinghistoryview.exe /HistorySource 6'
        # define output file
        webhist_output = r'\\' + smb_data + '\\' + createt + '-' + os.environ[
            'COMPUTERNAME'] + '-webhist-' + user_dir + '.csv'
        # define paths to different browser's history files
        ie5to9_history_dir = 'c:\\users\\' + user_dir
        ie10_cache_dir = 'c:\\users\\' + user_dir + '\\appdata\\local\microsoft\\windows\\webcache\\'
        ie10_tmp_cache_dir = 'c:\\users\\' + user_dir + '\\appdata\\local\microsoft\\windows\\webcache_tmp\\'
        ff_profile_dir = 'c:\\users\\' + user_dir + '\\appdata\\roaming\\mozilla\\firefox\\profiles\\'
        chrome_profile_dir = 'c:\\users\\' + user_dir + '\\appdata\\local\\google\\chrome\\user data\\'
        # IE5-9 History
        if os.path.exists(ie5to9_history_dir):
            bhv_command = bhv_command + ' /CustomFiles.IEFolders "' + ie5to9_history_dir + '"'
        # IE10+ History
        if os.path.exists(ie10_cache_dir + 'webcachev01.dat'):
            # create temp webcache folder for IE10+
            if not os.path.exists(ie10_tmp_cache_dir):
                os.makedirs(ie10_tmp_cache_dir)
            # copy contents of IE webcache to temp webcache folder
            for i in os.listdir(ie10_cache_dir):
                subprocess.call(
                    '\\\\' + smb_bin + '\\RawCopy\\RawCopy.exe ' + ie10_cache_dir + i + ' ' + ie10_tmp_cache_dir,
                    startupinfo=si)
            # insure webcachev01.dat is "clean" before parsing
            subprocess.call('esentutl /r V01 /d', cwd=ie10_tmp_cache_dir)
            bhv_command = bhv_command + ' /CustomFiles.IE10Files "' + ie10_tmp_cache_dir + 'webcachev01.dat"'
        # Firefox History
        first_history = True
        if os.path.exists(ff_profile_dir):
            ff_profiles = next(os.walk(ff_profile_dir))[1]
            for ff_profile in ff_profiles:
                if os.path.exists(ff_profile_dir + ff_profile + '\\places.sqlite'):
                    if first_history:
                        bhv_command = bhv_command + ' /CustomFiles.FirefoxFiles "' + ff_profile_dir + ff_profile + '\\places.sqlite"'
                        first_history = False
                    else:
                        bhv_command = bhv_command + ',"' + ff_profile_dir + ff_profile + '\\places.sqlite"'
        # Chrome History
        first_history = True
        if os.path.exists(chrome_profile_dir):
            # get default chrome profile
            chrome_profile_dirs = glob.glob(chrome_profile_dir + 'default*') + glob.glob(
                chrome_profile_dir + 'profile*')
            for chrome_profile in chrome_profile_dirs:
                if os.path.exists(chrome_profile + '\\history'):
                    if first_history:
                        bhv_command = bhv_command + ' /CustomFiles.ChromeFiles "' + chrome_profile + '\\history"'
                        first_history = False
                    else:
                        bhv_command = bhv_command + ',"' + chrome_profile + '\\history"'
        # Parse history files
        bhv_command = bhv_command + ' /sort "Visit Time" /VisitTimeFilterType 1 /scomma "' + webhist_output + '"'
        if not silent:
            print(bhv_command)
        subprocess.call(bhv_command, startupinfo=si)
        # Hash output file
        g = open(r'\\' + smb_data + r'\\' + createt + '-' + os.environ['COMPUTERNAME'] + '-' + 'sha256-hashing.log',
                 'a')
        g.write("%s - %s \n\n" % (webhist_output, hashfile(webhist_output)))
        # Remove temp webcache folder for IE10+
        if os.path.exists(ie10_tmp_cache_dir):
            shutil.rmtree(ie10_tmp_cache_dir)


def prefetch(tool_server, output_server, silent):
    """ Prefetch collection module """
    createt = strftime('%Y%m%d%H%M%S', gmtime())

    try:
        smb_bin = tool_server + r'\tools'

        smb_data = output_server + r'\data' + r'\prefetch-' + os.environ['COMPUTERNAME'] + r'\\' + createt

        if not os.path.exists(r'\\' + smb_data):
            os.makedirs(r'\\' + smb_data)

        if not silent:
            print('\nSaving output to ' + r'\\' + smb_data)

        user_dirs = next(os.walk('c:\\windows\\prefetch\\'))[2]
        b = True
        for f in user_dirs:
            if f.endswith(".pf"):
                cmd = r'\\' + smb_bin + r'\winprefetchview\winprefetchview.exe'
                cmd = cmd + r' /prefetchfile ' + r'c:\windows\prefetch\\' + f + r' /scomma ' + r'\\' + smb_data + '\\' + f + r'.csv'

                p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                p.communicate()

                if b:
                    b = False
                    smb_data2 = r'\\' + output_server + r'\data' + r'\prefetch-' + os.environ[
                        'COMPUTERNAME'] + r'\\' + createt + r'\Main'
                    if not os.path.exists(smb_data2):
                        os.makedirs(smb_data2)

                    cmd_main = r'\\' + smb_bin + r'\winprefetchview\winprefetchview.exe'
                    cmd_main = cmd_main + r' /scomma ' + smb_data2 + '\\' + r'Global-Prefetch' + r'.csv'

                    p2 = subprocess.Popen(cmd_main, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    p2.communicate()

    except:
        print("Some PF files cannot be read")
        pass


def collect(tool_server, output_server, silent):
    """ Artifact Collection Module """

    smb_bin=tool_server + r'\tools' # TOOLS Read-only share with third-party binary tools

    smb_data=output_server + r'\data' + r'\collection-' + os.environ['COMPUTERNAME'] # DATA Write-only share for output data
    if not os.path.exists(r'\\'+smb_data):
        os.makedirs(r'\\'+smb_data)

    if not silent:
        print ('\nSaving output to '+r'\\'+smb_data)

    recivedt=strftime('%Y%m%d%H%M%S', gmtime()) # Timestamp in GMT
    
    tool=('\\CyLR\CyLR.exe -o \\\\' + smb_data + ' -of ' + os.environ['COMPUTERNAME'] + '.zip') # Send output to output server
    print (tool)
        
    fullcommand=tool.split()
    commandname=fullcommand[0].split('.')

    if not silent:
        print ('\nDumping artifacts to ' +r'\\'+smb_data+r'\\'+ os.environ['COMPUTERNAME'] +'.zip\n')

    subprocess.call(r'\\'+smb_bin+r'\\'+tool)

    with open(r'\\' + smb_data + r'\\' + recivedt + '-sha256-hashing.log', 'a') as g:
        g.write("%s - %s \n\n" % (r'\\'+smb_data+r'\\'+ os.environ['COMPUTERNAME'] +'.zip', hashfile(r'\\'+smb_data+r'\\'+os.environ['COMPUTERNAME']+'.zip')))


def main():
    parser = ArgumentParser(description='::Rastrea2r RESTful remote Yara/Triage tool for Incident Responders ::')

    subparsers = parser.add_subparsers(dest="mode", help='modes of operation')

    """ Yara filedir mode """

    list_parser = subparsers.add_parser('yara-disk', help='Yara scan for file/directory objects on disk')
    list_parser.add_argument('path', action='store', help='File or directory path to scan')
    list_parser.add_argument('server', action='store', help='rastrea2r REST server')
    list_parser.add_argument('rule', action='store', help='Yara rule on REST server')
    list_parser.add_argument('-s', '--silent', action='store_true', help='Suppresses standard output')

    """Yara memory mode"""

    list_parser = subparsers.add_parser('yara-mem', help='Yara scan for running processes in memory')
    list_parser.add_argument('server', action='store', help='rastrea2r REST server')
    list_parser.add_argument('rule', action='store', help='Yara rule on REST server')
    list_parser.add_argument('-s', '--silent', action='store_true', help='Suppresses standard output')

    """Memory acquisition mode"""

    list_parser = subparsers.add_parser('memdump', help='Acquires a memory dump from the endpoint')
    list_parser.add_argument('TOOLS_server', action='store', help='Binary tool server (SMB share)')
    list_parser.add_argument('DATA_server', action='store', help='Data output server (SMB share)')
    list_parser.add_argument('-s', '--silent', action='store_true', help='Suppresses standard output')

    """Triage mode"""

    list_parser = subparsers.add_parser('triage', help='Collects triage information from the endpoint')
    list_parser.add_argument('TOOLS_server', action='store', help='Binary tool server (SMB share)')
    list_parser.add_argument('DATA_server', action='store', help='Data output server (SMB share)')
    list_parser.add_argument('-s', '--silent', action='store_true', help='Suppresses standard output')

    """Web History mode"""

    list_parser = subparsers.add_parser('web-hist', help='Generates web history for specified user account')
    list_parser.add_argument('TOOLS_server', action='store', help='Binary tool server (SMB share)')
    list_parser.add_argument('DATA_server', action='store', help='Data output server (SMB share)')
    list_parser.add_argument('-u', '--username', action='store', default='all',
                             help='User account to generate history for')
    list_parser.add_argument('-s', '--silent', action='store_true', help='Suppresses standard output')

    """Prefetch View mode"""

    list_parser = subparsers.add_parser('prefetch', help='Generates prefetch view')
    list_parser.add_argument('TOOLS_server', action='store', help='Binary tool server (SMB share)')
    list_parser.add_argument('DATA_server', action='store', help='Data output server (SMB share)')
    list_parser.add_argument('-s', '--silent', action='store_true', help='Suppresses standard output')

    """Artifact Collection mode"""

    list_parser = subparsers.add_parser('collect', help='Acquires artifacts from the endpoint')
    list_parser.add_argument('TOOLS_server', action='store', help='Binary tool server (SMB share)')
    list_parser.add_argument('DATA_server', action='store', help='Data output server (SMB share)')
    list_parser.add_argument('-s', '--silent', action='store_true', help='Suppresses standard output')

    parser.add_argument('-v', '--version', action='version', version='%(prog)s ' + __version__)
    args = parser.parse_args()

    if args.mode == 'yara-disk':
        yaradisk(args.path, args.server, args.rule, args.silent)

    elif args.mode == 'yara-mem':
        yaramem(args.server, args.rule, args.silent)

    elif args.mode == 'memdump':
        memdump(args.TOOLS_server, args.DATA_server, args.silent)

    elif args.mode == 'triage':
        triage(args.TOOLS_server, args.DATA_server, args.silent)

    elif args.mode == 'web-hist':
        webhist(args.TOOLS_server, args.DATA_server, args.username, args.silent)

    elif args.mode == 'prefetch':
        prefetch(args.TOOLS_server, args.DATA_server, args.silent)

    elif args.mode == 'collect':
        collect(args.TOOLS_server, args.DATA_server, args.silent)


if __name__ == '__main__':
    main()
