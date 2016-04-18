"""setup.py controls the build, testing, and distribution of the egg"""

from setuptools import setup, find_packages
import re
import sys
import os.path


VERSION_REGEX = re.compile(r"""
    ^__version__\s=\s
    ['"](?P<version>.*?)['"]
""", re.MULTILINE | re.VERBOSE)

VERSION_FILE = os.path.join("disco_aws_automation", "version.py")


def get_version():
    """Reads the version from the package"""
    with open(VERSION_FILE) as handle:
        lines = handle.read()
        result = VERSION_REGEX.search(lines)
        if result:
            return result.groupdict()["version"]
        else:
            raise ValueError("Unable to determine __version__")


def get_requirements():
    """Reads the installation requirements from requirements.pip"""
    with open("requirements.pip") as f:
        lines = f.read().split("\n")
        lines_without_comments = filter(lambda l: not l.startswith('#'), lines)
        return lines_without_comments


setup(name='asiaq',
    version=get_version(),
    description="infrastructure automation for AWS",
    long_description="",
    # Get strings from http://www.python.org/pypi?%3Aaction=list_classifiers
    classifiers=[
        'Programming Language :: Python',
        'Topic :: Software Development :: Libraries :: Python Modules',
        'License :: OSI Approved :: BSD License'
    ],
    keywords='',
    author='The Disco Team',
    author_email='',
    url='',
    license='BSD',
    packages=find_packages(exclude=['ez_setup']),
    include_package_data=True,
    zip_safe=False,
    dependency_links=[
    ],
    scripts=[
        "bin/acfg1.py",
        "bin/disco_alarms.py",
        "bin/disco_autoscale.py",
        "bin/disco_aws.py",
        "bin/disco_bake.py",
        "bin/disco_creds.py",
        "bin/disco_dynamodb.py",
        "bin/disco_iam.py",
        "bin/disco_eip.py",
        "bin/disco_elb.py",
        "bin/disco_route53.py",
        "bin/disco_log_metrics.py",
        "bin/disco_accounts.py",
        "bin/disco_rds.py",
        "bin/disco_elasticache.py",
        "bin/disco_vpc_ui.py",
        "bin/disco_metrics.py",
        "bin/disco_ssh.sh",
        "bin/disco_purge_snapshots.py",
        "bin/disco_app_auth.py",
        "bin/disco_deploy.py",
        "bin/disco_ssh.py",
        "bin/disco_chaos.py",
        "bin/disco_snapshot.py",
        "bin/disco_es.py",
        "jenkins/bake_common.sh",
        "jenkins/bake_all_phase1.sh",
        "jenkins/bake_all_phase2.sh",
        "jenkins/bake_hostclass.sh",
        "jenkins/bake_pipeline.sh",
        "jenkins/base_aws.config",
        "jenkins/base_boto.cfg",
        "jenkins/boto_init.sh",
        "jenkins/disco_app_auth_update.sh",
        "jenkins/record_job_status.sh"
    ],
    package_data={ "disco_aws_automation": ["../disco_aws.ini", "../disco_vpc.ini",
                                            "../jenkins/base_boto.cfg", "../jenkins/base_aws.config"] },
    install_requires=get_requirements(),
    test_suite = 'nose.collector',
    entry_points="""
        [paste.app_factory]
        main=disco_aws_automation:main
    """,
)
