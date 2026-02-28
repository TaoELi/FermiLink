Contributing
============

When contributing to FermiLink, follow the guidelines below:

Development setup
-----------------

First, we need to set up the development environment. Run:

.. code-block:: bash

   git clone https://github.com/TaoELi/FermiLink.git
   cd FermiLink/
   pip install -e ".[dev,docs]"

Documentation
~~~~~~~~~~~~~~~

After code contribution, write documentation in ``docs/source/`` and update the table of contents in ``docs/source/index.rst`` if necessary.

The documentation website can be built locally with (at the repo root):

.. code-block:: bash

   make doc html

Unit tests
~~~~~~~~~~~~~~

Before a pull request, make sure to run the quality gates and unit tests below.

.. code-block:: bash

   # code quality and style checks
   make lint
   make pretty
   # unit tests need to be passed
   pytest -q


