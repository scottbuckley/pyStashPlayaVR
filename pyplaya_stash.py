import asyncio
from io import BytesIO
import logging
import math
from datetime import datetime, timezone

from aiohttp import ClientError, ClientSession, web
from PIL import Image
import stashapi.log as log
from stashapi.stashapp import StashInterface


##### config

# the port for this webserver
PORT = 80

# Logging level (you probably don't need to change this)
LOG_LEVEL = logging.INFO

# the address at which this script, and PlayaVR, will access a Stash instance
STASH_SCHEME       = "http"
STASH_CONNECT_HOST = "127.0.0.1"
STASH_PORT         = "9999"

# Leave as None to use the hostname or IP address PlayaVR used to reach this
# server. Set a full URL such as "http://192.168.1.33:9999" to override it.
STASH_PUBLIC_URL_OVERRIDE = None

# VR tag mappings (you probably don't need to change these)
# Tag names are matched case-insensitively. The first match wins.
VR_TAG_FORMATS = {
    "Fisheye": ("FSH", "LR"),
    "180°":     ("180", "LR")
}

# Generic VR fallback (you probably don't need to change these)
VR_FALLBACK_TAG = "Virtual Reality"
VR_FALLBACK_FORMAT = ("180", "LR")

##### end config


API_BASE = "/api/playa/v2/"
stash = StashInterface({
    "scheme": STASH_SCHEME,
    "host":   STASH_CONNECT_HOST,
    "port":   STASH_PORT,
    "logger": log
})

def wrapJSON(data):
  return {
    "status": {
      "code": 1, "message": "ok"
    }, "data": data }


def stashBaseURL(request):
  if STASH_PUBLIC_URL_OVERRIDE:
    return STASH_PUBLIC_URL_OVERRIDE.rstrip('/')

  host = request.url.host
  if ':' in host:
    host = f"[{host}]"
  return f"{STASH_SCHEME}://{host}:{STASH_PORT}"

def serverBaseURL(request):
  return f"{request.scheme}://{request.host}"


#### URL handlers

routes = web.RouteTableDef()

@routes.get(API_BASE+'version')
async def webGetVersion(request):
  print('/version requested')
  return web.json_response(wrapJSON("1.0.0"))

@routes.get(API_BASE+'config')
async def webGetConfig(request):
  print('/config requested')
  return web.json_response(wrapJSON({
    "site_name": "pyPlaya",
    "auth": False,
    "actors": False,
    "categories": True,
    "studios": False,
    "categories_groups": False,
    "scripts": False,
    "masks": False,
    "analytics": False
  }))

@routes.get(API_BASE+'categories')
async def webGetCategories(request):
  print('/categories requested')
  tags = await asyncio.to_thread(stash.find_tags)
  cats = [{'id': t['id'], 'title': t['name']} for t in tags]
  return web.json_response(wrapJSON(cats))

def timestamp(date_str_iso):
  date = datetime.fromisoformat(date_str_iso.replace('Z', '+00:00'))
  if date.tzinfo is None:
    date = date.replace(tzinfo=timezone.utc)
  return int(date.timestamp())

def preview_image(request, idd):
  return f"{serverBaseURL(request)}/thumbnail/{idd}"

def stream_url(request, idd):
  return f"{stashBaseURL(request)}/scene/{idd}/stream"

def video_format(tags):
  tag_names = {tag['name'].casefold() for tag in tags}
  for tag_name, video_format in VR_TAG_FORMATS.items():
    if tag_name.casefold() in tag_names:
      return video_format
  if VR_FALLBACK_TAG.casefold() in tag_names:
    return VR_FALLBACK_FORMAT
  return ('FLT', 'MN')

def convert_webp_to_png(data):
  with Image.open(BytesIO(data)) as image:
    output = BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()

async def http_client_context(app):
  async with ClientSession() as session:
    app['http_client'] = session
    yield

@routes.get('/thumbnail/{idd}')
async def webGetThumbnail(request):
  try:
    idd = int(request.match_info['idd'])
  except (KeyError, TypeError, ValueError):
    raise web.HTTPBadRequest(reason="thumbnail id must be an integer")

  stash_url = f"{STASH_SCHEME}://{STASH_CONNECT_HOST}:{STASH_PORT}/scene/{idd}/screenshot"
  try:
    async with request.app['http_client'].get(stash_url) as response:
      data = await response.read()
      status = response.status
      content_type = response.content_type
  except ClientError as error:
    raise web.HTTPBadGateway(reason=f"could not retrieve thumbnail from Stash: {error}")

  if status != 200:
    return web.Response(status=status, body=data, content_type=content_type)

  if content_type == 'image/webp':
    try:
      data = await asyncio.to_thread(convert_webp_to_png, data)
    except OSError as error:
      raise web.HTTPBadGateway(reason=f"could not convert WebP thumbnail: {error}")
    content_type = 'image/png'

  return web.Response(
    body=data,
    content_type=content_type,
    headers={'Cache-Control': 'no-cache'}
  )

@routes.get(API_BASE+'videos')
async def webGetVideos(request):
  try:
    pageIndex = int(request.query['page-index'])
    pageSize  = int(request.query['page-size'])
  except (KeyError, ValueError):
    raise web.HTTPBadRequest(reason="page-index and page-size must be integers")

  if pageIndex < 0 or pageSize < 1:
    raise web.HTTPBadRequest(reason="page-index must be non-negative and page-size must be positive")

  order     = request.query.get('order', 'release_date')
  direction = request.query.get('direction', 'asc')
  title     = request.query.get('title')
  cats      = request.query.get('included-categories', '')
  excluded_cats = request.query.get('excluded-categories', '')
  
  # categories
  if (cats == ''):
    cats = []
  else:
    cats = cats.split(',')

  if (excluded_cats == ''):
    excluded_cats = []
  else:
    excluded_cats = excluded_cats.split(',')

  # ordering
  order_map = {
    'title': 'title',
    'release_date': 'created_at',
    'popularity': 'play_count'
  }
  if order not in order_map:
    raise web.HTTPBadRequest(reason=f"unsupported order: {order}")
  if direction not in ('asc', 'desc'):
    raise web.HTTPBadRequest(reason=f"unsupported direction: {direction}")

  order_str = order_map[order]
  direction_str = direction.upper()

  #query
  result = await asyncio.to_thread(stash.call_GQL, """
    query getScenes($perpage: Int, $page: Int, $order: String, $dir: SortDirectionEnum, $title: String, $cats: [ID!], $excludedCats: [ID!]) {
      findScenes(filter: { per_page: $perpage, page: $page, sort: $order, direction: $dir, q: $title }
           scene_filter: { tags: { modifier: INCLUDES_ALL, value: $cats, excludes: $excludedCats } }) {
        count
        scenes {
          id
          title
          date
          created_at
          files {
            basename
            duration
          }
        }
      }
  }""", {
    'perpage': pageSize,
    'page': pageIndex+1,
    'order': order_str,
    'dir': direction_str,
    'title': title,
    'cats': cats,
    'excludedCats': excluded_cats
    })
  scenes = result['findScenes']
  
  scene_count = scenes['count']
  scenes = scenes['scenes']
  page_count = max(1, math.ceil(int(scene_count)/int(pageSize)))

  scenes_output = []
  for s in scenes:
    title = s['title'] or (s['files'][0]['basename'] if s['files'] else f"Scene {s['id']}")
    duration_seconds = round(s['files'][0].get('duration') or 0) if s['files'] else 0
    s_out = {
      'id': s['id'],
      'title': title,
      'preview_image': preview_image(request, s['id']),
      'details': [{
        'type': 'full',
        'duration_seconds': duration_seconds
      }]
    }

    # fix missing dates
    if (s['date'] != None):
      s_out['release_date'] = timestamp(s['date'])
    else:
      s_out['release_date'] = timestamp(s['created_at'])


    scenes_output.append(s_out)
  
  sceneData = {
    "page_index": pageIndex,
    "page_size": pageSize,
    "page_total": page_count,
    "item_total": scene_count,
    "content": scenes_output
  }
  
  return web.json_response(wrapJSON(sceneData))

@routes.get(API_BASE+'video/{idd}')
async def webGetVideo(request):
  try:
    idd = int(request.match_info['idd'])
  except (KeyError, TypeError, ValueError):
    raise web.HTTPBadRequest(reason="video id must be an integer")

  result = await asyncio.to_thread(stash.call_GQL, """
    query getScene($id: ID!) {
      findScene(id: $id) {
        id
        title
        date
        created_at
        description: details
        files {
          basename
          duration
        }
        tags {
          id
          name
        }
      }
  }""", {'id': idd})
  s = result['findScene']

  if s is None:
    raise web.HTTPNotFound(reason="video not found")

  title = s['title'] or (s['files'][0]['basename'] if s['files'] else f"Scene {s['id']}")
  duration_seconds = round(s['files'][0].get('duration') or 0) if s['files'] else 0
  release_date = timestamp(s['date'] or s['created_at'])
  projection, stereo = video_format(s['tags'])

  scene = {
    'id': s['id'],
    'title': title,
    'description': s['description'],
    'release_date': release_date,
    'preview_image': preview_image(request, s['id']),
    'categories': [
      {'id': tag['id'], 'title': tag['name']}
      for tag in s['tags']
    ],
    'details': [{
      'type': 'full',
      'duration_seconds': duration_seconds,
      'links': [{
        'is_stream': True,
        'is_download': False,
        'projection': projection,
        'stereo': stereo,
        'url': stream_url(request, s['id'])
      }]
    }]
  }

  return web.json_response(wrapJSON(scene))

app = web.Application()
app.cleanup_ctx.append(http_client_context)
app.add_routes(routes)
logging.basicConfig(level=LOG_LEVEL)
web.run_app(app, port=PORT, access_log_format=" :: %r %s %T %t")

# async def handle(request):
#   name = request.match_info.get('name', "Anonymous")
#   text = "Hello, " + name
#   print('Request served!')
#   return web.Response(text=text)



# async def run_web_server():
#   app = web.Application()
#   app.add_routes([web.get('/api/playa/v2/version', webGetVersion),
#                   web.get('/{name}', handle)])
#   runner = web.AppRunner(app)
#   # await runner.setup()
#   # site = web.TCPSite(runner, 'localhost', 80)
#   # await site.start()

# loop = asyncio.get_event_loop()
# loop.create_task(run_web_server())
# loop.run_forever()
