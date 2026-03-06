Contributing
============

Follow these guidelines when contributing to FermiLink.

Development setup
-----------------

Start by setting up the development environment:

.. code-block:: bash

   git clone https://github.com/TaoELi/FermiLink.git
   cd FermiLink/
   pip install -e ".[dev,docs]"

Documentation
~~~~~~~~~~~~~

After making code changes, update the documentation in ``docs/source/`` and the table of contents in ``docs/source/index.rst`` if necessary.

Build the documentation website locally with (from the repo root):

.. code-block:: bash

   make doc html

Unit tests
~~~~~~~~~~

Before opening a pull request, run the quality checks and unit tests:

.. code-block:: bash

   # code quality and style checks
   make lint
   make pretty
   # unit tests need to be passed
   pytest -q


