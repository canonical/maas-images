import datetime
import os

import yaml

# Configuration for the Sphinx documentation builder.
# All configuration specific to your project should be done in this file.
#
# If you're new to Sphinx and don't want any advanced or custom features,
# just go through the items marked 'TODO'.
#
# A complete list of built-in Sphinx configuration values:
# https://www.sphinx-doc.org/en/master/usage/configuration.html
#
# Our starter pack uses the custom Canonical Sphinx extension
# to keep all documentation based on it consistent and on brand:
# https://github.com/canonical/canonical-sphinx


#######################
# Project information #
#######################

# Project name
project = "MAAS Images"
author = "Canonical Ltd."


# Sidebar documentation title; best kept reasonably short
html_title = project + " documentation"


# Copyright string; shown at the bottom of the page
copyright = "2012-%s AGPL, %s" % (datetime.date.today().year, author)


# Documentation website URL
ogp_site_url = "https://canonical-maas-images.readthedocs-hosted.com/"


# Preview name of the documentation website
ogp_site_name = project


# Preview image URL
ogp_image = "https://assets.ubuntu.com/v1/cc828679-docs_illustration.svg"


# Product favicon; shown in bookmarks, browser tabs, etc.
# html_favicon = '.sphinx/_static/favicon.png'


# Dictionary of values to pass into the Sphinx context for all pages:
# https://www.sphinx-doc.org/en/master/usage/configuration.html#confval-html_context

html_context = {
    # Product tag image; the orange part of your logo, shown in the page header
    "repo_default_branch": "master",
    # Docs location in the repo; used in links for viewing the source files
    "repo_folder": "/docs/",
    # Valid options: none, prev, next, both
    # "sequential_nav": "both",
    "display_contributors": False,
}

html_extra_path = []

# Allow opt-in build of the OpenAPI "Hello" example so docs stay clean by default.
if os.getenv("OPENAPI", ""):
    tags.add("openapi")
    html_extra_path.append("how-to/assets/openapi.yaml")

# Project slug; see https://meta.discourse.org/t/what-is-category-slug/87897
# slug = 'maas-images'

#######################
# Sitemap configuration: https://sphinx-sitemap.readthedocs.io/
#######################

# Use RTD canonical URL to ensure duplicate pages have a specific canonical URL

html_baseurl = os.environ.get("READTHEDOCS_CANONICAL_URL", "/")

# sphinx-sitemap uses html_baseurl to generate the full URL for each page:

sitemap_url_scheme = "{link}"

# Include `lastmod` dates in the sitemap:

sitemap_show_lastmod = True

# Exclude generated pages from the sitemap:

sitemap_excludes = [
    "404/",
    "genindex/",
    "search/",
]

#######################
# Template and asset locations
#######################

# html_static_path = ["_static"]
# templates_path = ["_templates"]


#############
# Redirects #
#############

# To set up redirects: https://documatt.gitlab.io/sphinx-reredirects/usage.html
# For example: 'explanation/old-name.html': '../how-to/prettify.html',

# To set up redirects in the Read the Docs project dashboard:
# https://docs.readthedocs.io/en/stable/guides/redirects.html

# NOTE: If undefined, set to None, or empty,
#       the sphinx_reredirects extension will be disabled.

redirects = {}


###########################
# Link checker exceptions #
###########################

# A regex list of URLs that are ignored by 'make linkcheck'
#
# TODO: Remove or adjust the ACME entry after you update the contributing guide

linkcheck_ignore = ["http://127.0.0.1:8000", "https://github.com/canonical/ACME/*"]


# A regex list of URLs where anchors are ignored by 'make linkcheck'

linkcheck_anchors_ignore_for_url = [r"https://github\.com/.*"]

# give linkcheck multiple tries on failure
# linkcheck_timeout = 30
linkcheck_retries = 3

########################
# Configuration extras #
########################

# Custom MyST syntax extensions; see
# https://myst-parser.readthedocs.io/en/latest/syntax/optional.html
#
# NOTE: By default, the following MyST extensions are enabled:
#       substitution, deflist, linkify

# myst_enable_extensions = set()


# Custom Sphinx extensions; see
# https://www.sphinx-doc.org/en/master/usage/extensions/index.html

# NOTE: The canonical_sphinx extension is required for the starter pack.

extensions = [
    "canonical_sphinx",
    "notfound.extension",
    "sphinx_design",
    "sphinx_reredirects",
    "sphinx_tabs.tabs",
    "sphinxcontrib.jquery",
    "sphinxext.opengraph",
    "sphinx_config_options",
    "sphinx_contributor_listing",
    "sphinx_filtered_toctree",
    "sphinx_related_links",
    "sphinx_roles",
    "sphinx_terminal",
    "sphinx_ubuntu_images",
    "sphinx_youtube_links",
    "sphinxcontrib.cairosvgconverter",
    "sphinx_last_updated_by_git",
    "sphinx.ext.intersphinx",
    "sphinx_sitemap",
    "myst_parser",
]

# Additional MyST syntax
myst_enable_extensions = {
    "substitution",
    "deflist",
    "linkify",
    "attrs_block",
}


# Excludes files or directories from processing

exclude_patterns = [
    "doc-cheat-sheet*",
]

# Adds custom CSS files, located under 'html_static_path'

# html_css_files = []


# Adds custom JavaScript files, located under 'html_static_path'

# html_js_files = []


# Specifies a reST snippet to be appended to each .rst file

rst_epilog = """
.. include:: /reuse/links.txt
.. include:: /reuse/substitutions.txt
"""

# Feedback button at the top; enabled by default
disable_feedback_button = True


# Specifies a reST snippet to be prepended to each .rst file
# This defines a :center: role that centers table cell content.
# This defines a :h2: role that styles content for use with PDF generation.

rst_prolog = """
.. role:: center
   :class: align-center
.. role:: h2
    :class: hclass2
.. role:: woke-ignore
    :class: woke-ignore
.. role:: vale-ignore
    :class: vale-ignore
"""

# Workaround for https://github.com/canonical/canonical-sphinx/issues/34

if "discourse_prefix" not in html_context and "discourse" in html_context:
    html_context["discourse_prefix"] = html_context["discourse"] + "/t/"

# Workaround for substitutions.yaml

if os.path.exists("./reuse/substitutions.yaml"):
    with open("./reuse/substitutions.yaml", "r") as fd:
        myst_substitutions = yaml.safe_load(fd.read())

# Add configuration for intersphinx mapping

intersphinx_mapping = {}
