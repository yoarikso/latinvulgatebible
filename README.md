# Latin Vulgate - Biblia Sacra Vulgata (VULGATE) Catholic Bible in JSON

This vulgate.py project, written in Python, converts the Latin Vulgate - Biblia Sacra Vulgata (VULGATE) Catholic Bible into JSON format.

## Overview

The Python script `vulgate.py` downloads the HTML files from the BibleGateway website and converts them into JSON format. The JSON files are saved in the vulgate-json directory with both Old Testament (OT) and New Testament (NT) books.

The script also creates a combined JSON file called EntireBible-VULGATE.json.

## Usage

To encode the entire Bible into JSON format, run the following command:

```bash
python3 vulgate.py -e (--encode-bible)
```

To merge the JSON files into a single EntireBible-VULGATE.json file, run the following command:

```bash
python3 vulgate.py -m (--merge-bible)
```

To check the integrity of the JSON files, run the following command:

```bash
python3 vulgate_checkintegrity.py
```

## Updates

- **March 2026**: Initial release.
