import datetime
import enum
import math
import os
import subprocess
import tempfile

from algorithms import *
from sshmessage import *

def score(issues):
    max_score = {}

    for issue in issues:
        if issue.what in max_score:
            max_score[issue.what] = max(max_score[issue.what], issue.severity.value)
        else:
            max_score[issue.what] = issue.severity.value

    return sum(max_score.values())

class ModuliFile(object):
    def __init__(self, moduli_file):
        self.__file = moduli_file

    def read(self):
        line = self.__file.readline()
        groups = []
        
        while line:
            parts = line.split(" ")
            groups.append(DHGEXGroup(generator=int(parts[5], 16), prime=int(parts[6], 16)))
            line = self.__file.readline()
        
        return groups

    def write(self, group):
        try:
            iter(group)

            for g in group:
                self.write(g)
        except TypeError as ex:
            print(
                datetime.datetime.now().strftime("%Y%m%d%H%M%S"),
                "0", # type
                "0", # tests
                "0", # trials
                str(math.floor(math.log(group.prime, 2))),
                hex(group.generator)[2:],
                hex(group.prime)[2:],
                file=self.__file
            )

def analyze_kex_init(kex_init):
    issues = []
    issues += analyze_kex_algorithms(kex_init)
    issues += analyze_host_key_algorithms(kex_init)
    downgrade_resistant = is_downgrade_resistant(issues)
    issues += analyze_authenticated_encryption(
        kex_init.encryption_algorithms_c2s,
        kex_init.mac_algorithms_c2s,
        downgrade_resistant
    )

    if not is_symmetric(kex_init):
        issues += analyze_authenticated_encryption(
            kex_init.encryption_algorithms_s2c,
            kex_init.mac_algorithms_s2c,
            downgrade_resistant
        )

    return issues

def is_downgrade_resistant(issues):
    for issue in issues:
        if issue.severity >= Severity.warning and issue.what in [ "weak key exchange hash", "small DH group" ]:
            return False
    return True

def is_symmetric(kex_init):
    if kex_init.encryption_algorithms_c2s != kex_init.encryption_algorithms_s2c:
        return False
    if kex_init.mac_algorithms_c2s != kex_init.mac_algorithms_s2c:
        return False
    return True 

def analyze_authenticated_encryption(encryption_algorithms, mac_algorithms, best_case):
    choices = []
    worst = []
    
    for cipher_algo in encryption_algorithms:
        if cipher_algo not in known_ciphers:
            choices.append(authenticated_encryption_issues(None, None, Issue(Severity.info, "unknown cipher", encr_algo)))
            continue

        cipher = known_ciphers[cipher_algo]

        if cipher.mode == CipherMode.AEAD:
            choices.append(authenticated_encryption_issues(cipher, None))
            continue
        
        for mac_algo in mac_algorithms:
            if mac_algo not in known_macs:
                choices.append(authenticated_encryption_issues(cipher, None, Issue(Severity.info, "unknown MAC", mac_algo)))
                continue

            mac = known_macs[mac_algo]
            choices.append(authenticated_encryption_issues(cipher, mac))

    for choice in choices:
        if best_case: return choice

        if score(worst) < score(choice):
            worst = choice

    return worst

def authenticated_encryption_issues(cipher, mac, *unknowns):
    issues = []

    if cipher:
        issues += cipher.issues

    if mac:
        issues += mac.issues

    if cipher and mac and cipher.mode == CipherMode.CBC and mac.mode == MACMode.EAM:
        issues.append(Issue(Severity.warning, "CBC-and-MAC"))

    issues += unknowns
    return issues
        
def analyze_kex_algorithms(kex_init):
    issues = []
    
    for algo in kex_init.kex_algorithms:
        issues += known_kex_algorithms.get(
            algo,
            [ Issue(Severity.info, "unknown key exchange algorithm", algo) ]
        )

    return issues

def analyze_host_key_algorithms(kex_init):
    return []

def analyze_dh_groups(dh_groups):
    issues = []

    for group in dh_groups:
        size = math.ceil(math.log(group.prime, 2))
        if size <= 2**10:
            issues.append(Severity.error, "small DH group", str(size) + " bits", group)
        elif size <= 2**10 + 2**9:
            issues.append(Severity.warning, "small DH group", str(size) + " bits", group)

    ( input_fd, input_name ) = tempfile.mkstemp()

    with open(input_fd, "w") as input_file:
        moduli = ModuliFile(input_file)
        moduli.write(dh_groups)

    ( output_fd, output_name ) = tempfile.mkstemp()
    safe_groups = []

    with open(output_fd, "r") as output_file:
        subprocess.check_output([ "ssh-keygen", "-T", output_name, "-f", input_name ])
        moduli = ModuliFile(output_file)
        safe_groups = moduli.read()
    
    os.unlink(input_name)
    os.unlink(output_name)

    if not dh_groups.issubset(safe_groups):
        for unsafe_group in dh_groups.difference(safe_groups):
            issues.append(Issue(Severity.critical, "unsafe DH group", unsafe_group))

    return issues