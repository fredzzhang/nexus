"""Setup script for nexus

Fred Zhang <fredzz@amazon.com>
"""

from setuptools import setup, find_packages

setup(
    name='nexus',
    version='0.1.0',
    packages=find_packages(),
    install_requires=[
        'boto3',
        'numpy',
        'opencv-python',
        'Pillow',
    ],
    python_requires='>=3.9',
)
