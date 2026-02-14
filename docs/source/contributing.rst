Contributing
============

FermiLink contributions should preserve consistency across ``web``, ``exec``,
and ``chat`` paths, since these modes share core routing and policy behavior.

Development setup
-----------------

.. code-block:: bash

   git clone https://github.com/TaoELi/FermiLink_development.git
   cd FermiLink_development
   pip install -e ".[dev,docs]"

Quality gates
-------------

.. code-block:: bash

   make lint
   pytest -q

Documentation workflow
----------------------

.. code-block:: bash

   make doc
   make html

Contribution guidelines
-----------------------

- Extend existing modules rather than duplicating behavior.
- Keep failure modes explicit and deterministic.
- Add or update tests for every behavior change.
- Update docs when command/runtime behavior changes.
- Keep package routing and overlay semantics aligned across modes.
