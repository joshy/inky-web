from flask import Flask, request, render_template_string
from PIL import Image, ImageOps
from inky.auto import auto
import io
import threading
import subprocess
import warnings

app = Flask(__name__)

# Positive PIL rotations are counter-clockwise.
# If the image appears rotated the wrong direction, change 90 to 270.
ROTATION = 90

HTML = """
<!doctype html>
<html>
<head>
    <title>Inky Picture Frame</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {
            font-family: sans-serif;
            max-width: 500px;
            margin: 40px auto;
            padding: 20px;
            text-align: center;
        }

        h1 {
            margin-bottom: 30px;
        }

        .card {
            padding: 25px;
            border: 1px solid #ccc;
            border-radius: 12px;
            margin-bottom: 25px;
        }

        input {
            font-size: 16px;
            width: 100%;
        }

        button {
            font-size: 18px;
            padding: 14px 22px;
            margin-top: 20px;
            cursor: pointer;
            border-radius: 8px;
            border: 0;
        }

        .update {
            background: #222;
            color: white;
        }

        .shutdown {
            background: #b00020;
            color: white;
        }

        .message {
            padding: 15px;
            margin-bottom: 20px;
            background: #eee;
            border-radius: 8px;
        }

        .small {
            color: #666;
            font-size: 14px;
        }
    </style>
</head>

<body>

<h1>Inky Picture Frame</h1>

{% if message %}
<div class="message">
    {{ message }}
</div>
{% endif %}

<div class="card">
    <h2>Change Picture</h2>

    <form method="post"
          action="/upload"
          enctype="multipart/form-data">

        <input
            type="file"
            name="image"
            accept="image/jpeg,image/png,image/webp"
            required>

        <br>

        <button class="update" type="submit">
            Update Display
        </button>

    </form>

    <p class="small">
        Images are automatically cropped to fit the portrait display.
    </p>
</div>

<div class="card">
    <h2>Finished?</h2>

    <p>
        Shut down the Raspberry Pi before disconnecting power.
    </p>

    <form method="post"
          action="/shutdown"
          onsubmit="return confirm('Shut down the Raspberry Pi?');">

        <button class="shutdown" type="submit">
            Shut Down
        </button>

    </form>
</div>

</body>
</html>
"""

update_lock = threading.Lock()

# The Inky driver grabs GPIO lines and an SPI handle in its constructor.
# Build it once and reuse it, so repeated uploads don't leak file
# descriptors or re-read the board EEPROM on every request.
_display = None


def get_display():
    global _display

    if _display is None:
        _display = auto()

    return _display


def update_inky(file_data):
    with update_lock:
        display = get_display()

        # Load image
        image = Image.open(io.BytesIO(file_data))

        # Respect phone/camera EXIF orientation
        image = ImageOps.exif_transpose(image)

        image = image.convert("RGB")

        width, height = display.resolution

        # Physical panel = 1600 x 1200
        #
        # Because our frame is rotated 90 degrees,
        # work in portrait orientation first:
        #
        # 1200 x 1600
        portrait_size = (height, width)

        image = ImageOps.fit(
            image,
            portrait_size,
            method=Image.Resampling.LANCZOS
        )

        # Rotate into the physical orientation expected
        # by the Inky panel.
        image = image.rotate(
            ROTATION,
            expand=True
        )

        display.set_image(image)

        # inky reports a refresh that never completed as a warning and
        # returns normally. Without this, a panel that ignored the update
        # would still be reported to the user as "updated successfully".
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")

            display.show()

        stalls = [
            str(w.message)
            for w in caught
            if "Wait: Timed out" in str(w.message)
        ]

        if stalls:
            raise RuntimeError(
                "The display did not complete its refresh "
                f"({stalls[0]}). The panel is not responding on its "
                "BUSY line - disconnect power from the Pi completely "
                "for ~30 seconds, then try again."
            )


@app.route("/", methods=["GET"])
def index():
    return render_template_string(
        HTML,
        message=None
    )


@app.route("/upload", methods=["POST"])
def upload():
    uploaded = request.files.get("image")

    if not uploaded or uploaded.filename == "":
        return render_template_string(
            HTML,
            message="Please choose an image."
        )

    try:
        update_inky(uploaded.read())

        return render_template_string(
            HTML,
            message="Picture updated successfully."
        )

    except Exception as e:
        return render_template_string(
            HTML,
            message=f"Error: {e}"
        )


def power_off():
    subprocess.run(
        ["sudo", "/usr/bin/systemctl", "poweroff"]
    )


@app.route("/shutdown", methods=["POST"])
def shutdown():
    # Give the browser enough time to receive the response
    # before networking disappears.
    timer = threading.Timer(
        2.0,
        power_off
    )

    timer.start()

    return """
    <!doctype html>
    <html>
    <head>
        <meta name="viewport"
              content="width=device-width, initial-scale=1">
        <style>
            body {
                font-family: sans-serif;
                text-align: center;
                padding: 60px 20px;
            }
        </style>
    </head>

    <body>
        <h1>Shutting down…</h1>

        <p>
            Wait about 10 seconds, then disconnect the power.
        </p>

        <p>
            The picture will remain on the Inky display.
        </p>
    </body>
    </html>
    """


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=80,
        debug=False
    )
