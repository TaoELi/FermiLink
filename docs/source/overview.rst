FermiLink
======================================

.. image:: _static/img/icon.png
   :alt: FermiLink icon
   :align: center
   :scale: 18

**FermiLink** lets you describe a scientific computing goal in a simple
markdown file and then handles everything else -- writing scripts, choosing
the right tools, submitting jobs, monitoring progress, and iterating until
the goal is met. It works the same way on your laptop, your lab's
workstation, or an HPC cluster.

.. figure:: _static/img/major_modes_workflow.svg
   :alt: Three major FermiLink workflows: exec for single runs, loop for iterative runs involving long SLURM or PID jobs, and research/reproduce for full research-paper-level calculations.
   :align: center
   :width: 95%

It ships with 150+ built-in scientific package knowledge bases
(``fermilink install``), and you can compile your own local packages, paper
pipelines, or group-specific tools into the knowledge base
(``fermilink compile``).

.. figure:: _static/img/package_management_workflow.svg
   :alt: FermiLink package management workflow.
   :align: center
   :width: 95%

Apart from the command line, it includes a **web UI** for a ChatGPT-style
interface and a **Telegram bot** for remote control from your phone.

.. figure:: _static/img/web_ui_telegram.png
   :alt: FermiLink web UI and Telegram bot.
   :align: center
   :width: 95%


.. toctree::
   :maxdepth: 1
   :caption: 🚀 Get Started

   quickstart
   installation
   tutorial_laptop
   tutorial_hpc
   choosing_agent

.. toctree::
   :maxdepth: 1
   :caption: 📖 Core Concepts

   How FermiLink Works <introduction>
   writing_goal_md
   Code Optimization <optimize>

.. toctree::
   :maxdepth: 1
   :caption: 📋 Guides

   CLI Commands <usage>
   Web UI <usage_web_ui>
   Telegram Bot <usage_chatting_apps>
   Package Management <scientific_packages>
   Building Your Own Package <usage_configure_your_package>
   Research Pipelines & Memory <usage_advanced_configuration>

.. toctree::
   :maxdepth: 1
   :caption: 🔧 Reference

   Configuration <configuration>
   architecture
   API Reference <api/modules>
   Built-in Packages <built_in_scientific_packages>

.. toctree::
   :maxdepth: 1
   :caption: 🤝 Community

   contributing
   citation
