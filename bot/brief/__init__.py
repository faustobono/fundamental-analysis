"""Genera el informe listo para pegar: prompt de Capa 2 + datos de Capa 1."""

from .prompt import DEFAULT_CONTEXT, build_prompt
from .render import render_data_block

__all__ = ["build_prompt", "render_data_block", "DEFAULT_CONTEXT"]
