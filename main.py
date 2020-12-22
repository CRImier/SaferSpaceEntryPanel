import json
import requests
import network
from machine import Pin
from TTP229_BSF import Keypad

## TODO: some docs, perhaps
# what to add if you want to support more than one keypad?

# what to add if you want to support more LEDs?
#   - just chain more 595 and increase shift_register_count

# "device working" LED connected to GPIOs
working_led = Pin(2, Pin.OUT)
working_led.value(False) # low = LED lit

# let's read the config
# we get the WiFi SSID, WiFi password, and HTTP endpoint from it
with open("config.json", "r") as f:
    config = json.load(f)

ssid = config["ssid"]
psk = config["psk"]
endpoint = config["endpoint"]

# setup WiFi - disable built-in AP and enable the STA interface

wlan = network.WLAN(network.AP_IF)
wlan.active(False)

wlan = network.WLAN(network.STA_IF)
wlan.active(True)

wlan.connect(ssid, psk)

# useful later on if we need to reconnect to WiFi
wlan_reset_seconds = 5
wlan_connect_seconds = 20

######################
# Hardware definitions
######################

kp = Keypad(sclk, sdo, inputs=16)

# we have 3 595 shift registers
shift_register_count = 3

# 595 SPI interface and pins
spi = SPI(1, sck=Pin(21), mosi=Pin(13), miso=Pin(12),
  baudrate=20*1000*1000, polarity=0, phase=0)
cs = Pin(11, Pin.OUT)

# LEDs connected to chained 595 ICs
# LEDs with two-pin tuple are dual-color LEDs where the color of the LED depends on the voltage polarity applied
# The only real benefit of those LEDs is that they're physically a single package and they might look better in terms of UX
# But I like them and I have a small package of those LEDs, so that's why I'm using them, even if it hurts repeatability a bit.
# Anyway, the code and the wiring allows for other LEDs to be used, with zero code modifications.
# The dual-color LEDs have to be wired in such a way where setting pin0 to high and pin1 to low would indicate "True" (i.e. green_
# and setting pin0 to low and pin1 to high would indicate "False" (i.e. red).
led_network_act = (0, 1)
led_status = (2, 3)
led_guests = (4, 5)
led_locations = (6, 7)
led_submittable = (22, 23)
# and these are single-pin LEDs used for highlighting different status buttons when they're selected
led_leaving = 8
leds_time = (9, 10, 11)
leds_guests = (12, 13, 14, 15)
leds_locations = (16, 17, 18, 19, 20, 21)

# Keys connected to the capacitative touch controller
# 4 status keys - "leaving" and time interval keys, all of these
key_leaving = 0
keys_time = (1, 2, 3)
# Keys for marking the amount of guests brought
keys_guests = (4, 5, 6, 7)
keys_locations = (8, 9, 10, 11, 12, 13)

key_submit = 14
key_clear = 15

#######################
# LED and 595 functions
#######################

# functions for changing the LED status
# these functions can work with either a simple LED that has an anode and a cathode, and only one color
# or two-pin dual LEDs where the LED color depends on the polarity applied

shift_reg_data = bytearray(shift_register_count)

def pin_high(pin):
    # set a specific bit high
    i, bit = divmod(pin, 8)
    mask = 1 << bit
    shift_reg_data[i] = shift_reg_data[i]|mask

def pin_low(pin):
    # set a specific bit low
    i, bit = divmod(pin, 8)
    mask = ~(1 << bit) & 0xff
    shift_reg_data[i] = shift_reg_data[i]&mask

def disable_led(led):
    if type(led) == int:
        pin_low(led)
    else:
        pin_low(led[0])
        pin_low(led[1])

def enable_led(led):
    if type(led) == int:
        pin_high(led)
    else:
        pin_high(led[0])
        pin_low(led[1])

def boolean_switch_led(led, state):
    if type(led) == int:
        pin_high(led) if state else pin_low(led)
    else:
        pin_high(led[0 if state else 1])
        pin_low(led[1 if state else 0])

def update_leds():
    # shifts the LED data out to the 595
    # until this function is called, none of the functions above will actually change the LEDs
    cs.off()
    spi.write(shift_reg_data)
    cs.on()

#######################
# Main keypad algorithm
#######################

# functions for changing the key scan algo states
# this code actually processes the buttons according to their meanings

# dict for managing the algorithm states
# "leaving" is either True or False
# "time" is the last key pressed out of the "time" keys
# "guests" is the last key pressed out of the "guests" keys
# "location" is a list of all keys pressed out of the "location" keys
states = {"leaving":False, "time":None, "guests":None, "locations":[]}

def reset_state():
    boolean_switch_led(led_status, False)
    boolean_switch_led(led_submittable, False)
    disable_led(led_network_act)
    disable_led(led_leaving)
    disable_led(led_guests)
    disable_led(led_locations)
    for led in leds_time+leds_guests+leds_locations:
        disable_led(led)
    update_leds()
    states["leaving"] = False
    states["time"] = None
    states["guests"] = None
    states["locations"] = []

def determine_submittable():
    # this function determines if the currently input state makes sense and can be submitted
    status_valid = states["leaving"] or states["time"] is not None
    if not status_valid:
        return False
    if states["leaving"]:
        # when "leaving", it doesn't matter whether "time", "locations" and "guests" options are selected
        return True
    else:
        # all of the "time", "locations" and "guests" options need to be picked
        if states["time"] is None: return False # cannot be None
        if states["guests"] is None: return False # cannot be None
        if not states["locations"]: return False # cannot be an empty list
        return True

def process_status_press(key):
    # Someone pressed one of the status keys:
    # a. Leaving
    # b. 5-30m time
    # c. 30m-2h time
    # d. 2h+ time
    # "Leaving" and "time" buttons have to be processed separately
    # Special cases:
    # 1) "leaving + time" option = can be combined to make an "I'm leaving for X minutes/hours" announcement
    if key == key_leaving:
        states["leaving"] = not states["leaving"]
        enable_led(led_leaving) if states["leaving"] else disable_led(led_leaving)
    else: # key is one of the "time" keys
        # whichever "time" key is pressed, we need to disable the LED that lights up the currently selected "time" key
        if states["time"] is not None:
            led_index = keys_status.index(states["time"])
            disable_led(leds_status[led_index])
        if key == states["time"]: # key pressed is the same as the currently pressed "time" key, we interpret it as "cancel"
            states["time"] = None
        else: # pressed key is not the same that was pressed before, new time picked, let's enable the LED that corresponds to it
            states["time"] = key
            led_index = keys_status.index(states["time"])
            enable_led(leds_status[led_index])
    # From here on, we'll assume that something has changed
    # If the "status" state isn't valid, we need to show the user that they need to pick more options
    # which is to say, we disable all LEDs and set the "status" LED to red (or whatever its "wrong state" color is)
    # A valid state is where the user has marked that they're leaving
    # (and might've picked "time" as "leaving for X time" in addition to that)
    # or where they've only picked "time" and then we can assume they're arriving
    status_valid = states["leaving"] or states["time"] is not None
    if not status_valid:
        reset_state()
        return
    # state is valid - we can proceed, let's mark the state as "valid"
    boolean_switch_led(led_status, True)
    if not states["leaving"]:
        # someone is arriving? they have to pick the "guests" and "locations" options
        # change the "guests" and "locations" LEDs according to whether something is actually selected there
        boolean_switch_led(led_guests, True if states["guests"] is not None else False)
        boolean_switch_led(led_locations, True if states["locations"] else False)
    else:
        # if "leaving", people might pick time/location/guests options, but they're not required to
        # so only mark them as "okay" if something's selected there, no need to mark them as "wrong" if nothing is selected
        disable_led(led_guests) if states["guests"] is not None else boolean_switch_led(led_guests, True)
        disable_led(led_locations) if not states["locations"] else boolean_switch_led(led_locations, True)
    # now, in the end, we need to check if the current data is submittable
    boolean_switch_led(led_submittable, determine_submittable())
    # aaand output the LEd states to the shift registers.
    update_leds()

def process_guests_press(key):
    # only one "guests" option can be picked
    if states["guests"] is not None:
        led_index = keys_guests.index(states["guests"])
        disable_led(leds_guests[led_index])
    if key == states["guests"]:
        states["guests"] = None
    else:
        states["guests"] = key
        led_index = keys_guests.index(states["guests"])
        enable_led(leds_guests[led_index])
    # now, let's see if the currently picked option is valid
    if states["leaving"]:
        disable_led(led_guests) if states["guests"] is not None else boolean_switch_led(led_guests, True)
    else:
        boolean_switch_led(led_guests, True if states["guests"] is not None else False)
    # does the submission make sense after the "guests" state change?
    boolean_switch_led(led_submittable, determine_submittable())
    update_leds()

def process_locations_press(key):
    # multiple "locations" options can be picked, so, states["locations"] is a list we add to or remove from
    if key in states["locations"]:
        led_index = keys_locations.index(key)
        disable_led(leds_locations[led_index])
        states["locations"].remove(key)
    else:
        states["locations"].append(key)
        led_index = keys_locations.index(key)
        enable_led(leds_locations[led_index])
    # now, let's see if the currently picked option is valid
    if states["leaving"]:
        disable_led(led_locations) if not states["locations"] else boolean_switch_led(led_locations, True)
    else:
        boolean_switch_led(led_locations, True if states["locations"] else False)
    # does the submission make sense after the "locations" state change?
    boolean_switch_led(led_submittable, determine_submittable())
    update_leds()

def process_submit_press():
    submittable = determine_submittable()
    if not submittable():
        # data doesn't make sense yet, doing nothing
        return # perhaps we could even blink the red LED at the "submittable" button, but, I guess, that's to be done later.
    # let's try and submit it
    boolean_switch_led(led_network_act, True)
    result = send_data()
    if not result:
        boolean_switch_led(led_network_act, False)
        sleep(1)
    # TODO: store data and resend at earliest convenience

def process_clear_press():
    reset_state()

#################
# Networking code
#################

# modify this as you see fit lol

def get_request_data():
    d = {}
    # take the "states" dict and transform it into something that the endpoint understands
    d["leaving"] = states["leaving"]
    # for these three options specifically, the "states" dict uses key pin numbers (for ease of key processing algorithm)
    # but we need key indices in their dict, essentially:
    # the input is "key pin number as it's connected to the TTP229 IC", so, could be 3, 10 or 16, depends on the wiring
    # the output is "index of the time option selected", so, 0, 1 or 2 (as we have 3 time options)
    # since, of course, the endpoint won't know which key is connected to which time button, and arguably it shouldn't matter.
    d["time"] = keys_time.index(states["time"])
    d["guests"] = keys_guests.index(states["guests"])
    d["locations"] = keys_locations.index(states["locations"])
    return d

def send_data():
    led_state = True
    # if we're currently not connected, let's "power cycle" the WiFi peripheral and try to reconnect
    if not wlan.isconnected():
        wlan.active(False)
        sleep(wlan_reset_seconds)
        wlan.active(True)
        wlan.connect(ssid, psk)
        connect_counter = 0
        while not wlan.isconnected():
            led_state = not led_state
            boolean_switch_led(led_network_act, led_state)
            sleep(1)
            connect_counter += 1
            if connect_counter == wlan_connect_seconds:
                break
        if not wlan.isconnected(): return False
    # presumably, we're connected.
    boolean_switch_led(led_network_act, True)
    data = get_request_data()
    try:
        r = requests.get(endpoint, data=data)
    except:
        boolean_switch_led(led_network_act, False)
        disable_led(led_working)
        raise
    else:
        if r.status_code == 200:
            return True
    return False

############################
# main loop - key processing
############################

pressed_key_dict = {i:False for i in range(16)}

while True:
    # mind you, keypad driver is set to only process a single keypress, but that's okay, as this code might be useful later.
    # we also don't process key release events yet, but we can add initial processing code for them, just in case.
    keys_just_pressed = []
    keys_just_released = []
    pressed_key_list = kp.read()
    for key, value in pressed_key_dict:
        if key in pressed_key_list and not value:
            # key just pressed
            pressed_key_dict[key] = True
            keys_just_pressed.append(key)
        elif key not in pressed_key_list and value:
            # key just released
            pressed_key_dict[key] = False
            keys_just_released.append(key)
    # now processing separate button types specifically
    for key in keys_just_pressed:
        if key == key_leaving or key in keys_time:
            process_status_key(key)
        elif key in keys_guests:
            process_guests_key(key)
        elif key in keys_locations:
            process_locations_key(key)
        elif key == key_submit:
            process_submit_key()
        elif key == key_clear:
            process_clear_key()
        else:
            print("Unprocessable key: {}".format(key))
    # no processing for just-released keys, basically, the code above is all you need for now.
    # "just released" can later be used for debouncing, for instance.
    # but then, so can this, lol:
    sleep(1)
