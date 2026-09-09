This is a python script that runs a webserver that you can point a PlayaVR app at, and it will serve videos to it, either from a local directory (`pyplaya_filesOnly.py`) or with metadata from a stash server (`pyplaya_stash.py`).

## Configuration

There are no command-line arguments at the moment. Configuration is in the section at the top of the Python script.

By default, the script connects to Stash on the same machine using `STASH_CONNECT_HOST = "127.0.0.1"`. URLs sent to PlayaVR use the hostname or IP address that PlayaVR used to reach this script, combined with the configured Stash scheme and port. For example, a request to `http://192.168.1.20` produces Stash URLs beginning with `http://192.168.1.20:9999`.

If automatic hostname detection is not appropriate for your network, set `STASH_PUBLIC_URL_OVERRIDE` to a full URL such as `"http://192.168.1.33:9999"`. Do not use `localhost` in this override because PlayaVR would interpret it as the headset itself.

Thumbnail requests are proxied through this script. JPEG and PNG covers are passed through, while WebP covers are converted to PNG because PlayaVR on Quest does not display WebP covers reliably.

## Requirements

- `aiohttp`
- `stashapi` (for Stash version)
- `Pillow` (for WebP thumbnail conversion)

Install them with:

```shell
python -m pip install -r requirements.txt
```

## Running

`python3 pyplaya_stash.py`
