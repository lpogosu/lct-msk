"""Резюме PDF с автораспознаванием (экран К7, концепция §3.1.1). Всё распознанное — «заявлено»."""

from app.services.resume.extract import ResumeError
from app.services.resume.parser import PARSER_VERSION, parse_resume, parse_text

__all__ = ["PARSER_VERSION", "ResumeError", "parse_resume", "parse_text"]
