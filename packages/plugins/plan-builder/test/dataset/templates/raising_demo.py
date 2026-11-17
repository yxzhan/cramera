"""
A stand-in for the generated demo of ${name} (${file_name}) that ends in an exception
whose message ends in a suggestion on a line of its own, as CRAM's exceptions do.
"""

DEMO = r"""
${demo}
"""

if __name__ == "__main__":
    raise RuntimeError("Nothing serves the query.\nSuggestion: start it.")
