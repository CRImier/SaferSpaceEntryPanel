import json
import network
from time import sleep, time, sleep_ms
import urequests as requests
from machine import Pin
from ttp229_bsf import Keypad

## TODO: some docs, perhaps
# what to add if you want to support more than one keypad?

# what to add if you want to support more LEDs?
#   - just chain more 595 and increase shift_register_count

# "device working" LED connected to GPIOs
working_led = Pin(2, Pin.OUT)
working_led.value(False) # low = LED lit

# timeout after last keypress when keypad resets to its idle state (i.e. user started input and then went away)
key_timeout = 10
press_tick_timeout = 3

wifi_is_setup = False
wlan = None
ssid = ""
psk = ""
endpoint = ""

# let's read the config
# we get the WiFi SSID, WiFi password, and HTTP endpoint from it

config = None

def load_config():
    global config
    try:
        with open("config.json", "r") as f:
            config = json.load(f)
    except:
        print("No WiFi config or wrong WiFi config file format")

def setup_wifi():
    global wifi_is_setup, wlan
    wifi_is_setup = False
    if config is None:
        return

    ssid = config["ssid"]
    psk = config["psk"]

    # setup WiFi - disable built-in AP and enable the STA interface

    wlan = network.WLAN(network.AP_IF)
    wlan.active(False)

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    wlan.connect(ssid, psk)
    wifi_is_setup = wlan.isconnected()

    sleep(1)
    # wifi needs to be disabled to not interfere with the touch pads
    # on bootup, we should indicate the network connectivity somewhat
    wlan.active(False)


# useful later on if we need to reconnect to WiFi
wlan_reset_seconds = 5
wlan_connect_seconds = 20

######################
# Hardware definitions
######################

sclk = Pin(22, Pin.OUT)
sdo = Pin(21, Pin.IN)
kp = Keypad(sclk, sdo, inputs=16, multi=True)

# we have 3 595 shift registers
shift_register_count = 3

# 595 interface and pins
din = Pin(25, Pin.OUT)
clk = Pin(27, Pin.OUT)
latch = Pin(26, Pin.OUT)

def shift_out(data):
    latch.off()
    for i in range(shift_register_count):
        byte = data[i]
        for j in range(8):
            value = byte & 1<<(7-j)
            din.value(value)
            clk.on()
            sleep_ms(1)
            clk.off()
    latch.on()

# LEDs connected to chained 595 ICs
# LEDs with two-pin tuple are dual-color LEDs where the color of the LED depends on the voltage polarity applied
# The only real benefit of those LEDs is that they're physically a single package and they might look better in terms of UX
# But I like them and I have a small package of those LEDs, so that's why I'm using them. You can easily replace them with two LEDs!
# Anyway, the code and the wiring allows for other LEDs to be used, with zero code modifications.
# The dual-color LEDs have to be wired in such a way where setting pin0 to high and pin1 to low would indicate "True" (i.e. green)
# and setting pin0 to low and pin1 to high would indicate "False" (i.e. red).
#led_network_act = (0, 1)
led_status = (6, 7)
led_guests = (4, 5)
led_locations = (2, 3)
led_submittable = (0, 1)
# These are single-pin LEDs used for highlighting different status buttons when they're selected
# I've used green LEDs
led_leaving = 18
leds_time = (19, 17, 16)
leds_guests = (22, 23, 21, 20)
leds_locations = (10, 11, 9, 8, 12, 13)

# Keys connected to the capacitative touch controller
# 4 status keys - "leaving" and time interval keys, all of these
key_leaving = 6
keys_time = (4, 3, 1)
# Keys for marking the amount of guests brought
keys_guests = (7, 5, 2, 0)
keys_locations = (8, 10, 13, 15, 9, 11)

key_submit = 12
key_clear = 14

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
    shift_out(shift_reg_data)

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
    #disable_led(led_network_act)
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
            led_index = keys_time.index(states["time"])
            disable_led(leds_time[led_index])
        if key == states["time"]: # key pressed is the same as the currently pressed "time" key, we interpret it as "cancel"
            states["time"] = None
        else: # pressed key is not the same that was pressed before, new time picked, let's enable the LED that corresponds to it
            states["time"] = key
            led_index = keys_time.index(states["time"])
            enable_led(leds_time[led_index])
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
        # so only mark them as "okay" if something's selected there, no need to mark them as "wrong" if "leaving" is selected
        disable_led(led_guests) if states["guests"] is None else boolean_switch_led(led_guests, True)
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
    if not submittable:
        # data doesn't make sense yet, doing nothing
        for i in range(5):
            disable_led(led_submittable)
            update_leds()
            sleep(0.1)
            boolean_switch_led(led_submittable, False)
            update_leds()
            sleep(0.1)
        return
    # let's try and submit it
    #boolean_switch_led(led_network_act, True)
    result = send_data()
    if not result:
        # send failed?
        # TODO: store data and resend at earliest convenience
        #boolean_switch_led(led_network_act, False)
        sleep(1)
    reset_state()

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
    d["time"] = keys_time.index(states["time"]) if states["time"] else None
    d["guests"] = keys_guests.index(states["guests"]) if states["time"] else None
    d["locations"] = [keys_locations.index(s) for s in states["locations"]]
    return d

def send_data():
    if not wifi_is_setup:
        print("Cannot send data - WiFi not set up!")
        return False
    led_state = True
    # if we're currently not connected, let's "power cycle" the WiFi peripheral and try to reconnect
    if not wlan.isconnected():
        if wlan.active():
            wlan.active(False)
            sleep(wlan_reset_seconds)
        wlan.active(True)
        wlan.connect(config["ssid"], config["psk"])
        connect_counter = 0
        while not wlan.isconnected():
            led_state = not led_state
            #boolean_switch_led(led_network_act, led_state)
            sleep(1)
            connect_counter += 1
            if connect_counter == wlan_connect_seconds:
                break
        if not wlan.isconnected(): return False
        wlan.active(False)
    # presumably, we're connected.
    #boolean_switch_led(led_network_act, True)
    data = get_request_data()
    if "data" in config:
        data.update(config["data"])
    try:
        pass #r = requests.get(config["endpoint"], data=data)
    except:
        #boolean_switch_led(led_network_act, False)
        working_led.value(True)
        raise
    else:
        return True
        if r.status_code == 200:
            return True
    return False

# mock function - as there's nowhere to send data to at the moment, I can just blink LEDs and clear
# this function overrides the previously defined send_data until commented out or renamed
def send_data():
    for i in range(5):
        disable_led(led_submittable)
        update_leds()
        sleep(0.15)
        boolean_switch_led(led_submittable, True)
        update_leds()
        sleep(0.15)
    disable_led(led_submittable)
    update_leds()
    return True

############################
# main loop - key processing
############################

pressed_key_dict = {i:False for i in range(16)}

# this loop processes keypresses and calls the functions appropriate for each key
# that's basically it. I guess later on it could do something else?
def main():
    last_press_time = None
    last_press_ticks = 0
    while True:
        # mind you, keypad driver is set to only process a single keypress, but that's okay, as this code might be useful later.
        # we also don't process key release events yet, but we can add initial processing code for them, just in case.
        keys_just_pressed = []
        keys_just_released = []
        pressed_key_list = kp.read()
        if pressed_key_list and last_press_ticks < press_tick_timeout:
            #print("Discarded keypress: ", pressed_key_list)
            continue
        for key, value in pressed_key_dict.items():
            if key in pressed_key_list and not value:
                # key just pressed
                print("Pressed: ", key)
                pressed_key_dict[key] = True
                keys_just_pressed.append(key)
                print(last_press_ticks)
                last_press_ticks = 0
            elif key not in pressed_key_list and value:
                # key just released
                #print("Released: ", key)
                pressed_key_dict[key] = False
            #    keys_just_released.append(key)
        # now processing separate button types specifically
        if keys_just_pressed:
            print("Pressed: ", keys_just_pressed)
            last_press_time = time()
        for key in keys_just_pressed:
            # mind you, in single keypress mode, we'll always be only processing one key at a time,
            # and this code will work for that either way.
            if key == key_leaving or key in keys_time:
                process_status_press(key)
            elif key in keys_guests:
                process_guests_press(key)
            elif key in keys_locations:
                process_locations_press(key)
            elif key == key_submit:
                if process_submit_press():
                    reset_state()
            elif key == key_clear:
                process_clear_press()
            else:
                print("Unprocessable key: {}".format(key))
        # last keypress more than X seconds ago? resetting
        if last_press_time and time()-last_press_time > key_timeout:
            #print("Keypress timeout")
            reset_state()
            last_press_time = None
        # 
        last_press_ticks += 1

reset_state()
load_config()
setup_wifi()
main()
