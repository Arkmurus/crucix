"""ARIA Search Engine — query layer over the curated index.

See `aria_service/search_engine/internal_search.py` for the public
`search()` entry point. Designed to be drop-in compatible with
`aria_service.intel.web_search.search()` so the chat path can call it
without further plumbing.
"""
