"""Shared Streamlit page chrome."""

from __future__ import annotations

import streamlit as st

CSS = """
<style>
    .stApp { background: #0e141b; color: #e8eef4; }
    h1, h2, h3 { letter-spacing: 0.01em; }
    .metric-row [data-testid="stMetric"] {
        background: #18222c;
        border: 1px solid #2a3a48;
        padding: 12px 16px;
        border-radius: 10px;
    }
    .transcript-line {
        background: #151d26;
        border: 1px solid #2a3a48;
        border-radius: 10px;
        padding: 12px 14px;
        margin-bottom: 10px;
    }
    .transcript-line.high {
        background: #3a1518;
        border: 1px solid #d64545;
    }
    .transcript-line.medium {
        background: #3a2e14;
        border: 1px solid #c9a227;
    }
    .meta { color: #9db0c0; font-size: 0.82rem; margin-bottom: 6px; }
    .speaker { font-weight: 700; text-transform: uppercase; }
    .alert-normal {
        background: #153224;
        border: 1px solid #2f8f5b;
        border-radius: 12px;
        padding: 18px;
    }
    .alert-high {
        background: #8b1518;
        border: 2px solid #ff5a5f;
        border-radius: 12px;
        padding: 18px;
        color: #fff;
    }
    .caution {
        margin-top: 14px;
        background: #111;
        border: 2px solid #ffd166;
        color: #ffd166;
        font-weight: 700;
        padding: 12px;
        border-radius: 8px;
        text-align: center;
    }
    .home-card {
        background: #18222c;
        border: 1px solid #2a3a48;
        border-radius: 16px;
        padding: 28px 24px;
        min-height: 180px;
    }
</style>
"""


def boot_page(title: str, layout: str = "wide") -> None:
    st.set_page_config(page_title=title, page_icon="⚖️", layout=layout)
    st.markdown(CSS, unsafe_allow_html=True)

