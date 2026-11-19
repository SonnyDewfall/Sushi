"""A stand-in for Sushi: a JACK client under pw-jack at the rig's quantum,
guitar (AUX0) to both outputs, as the rig wires it. Not Sushi: no plugins."""
import sys, threading, jack, numpy as np
client = jack.Client("sushi")
ins = [client.inports.register(f"audio_input_{i}") for i in range(2)]
outs = [client.outports.register(f"audio_output_{i}") for i in range(2)]
@client.set_process_callback
def process(frames):
    for i, o in zip(ins, outs):
        o.get_array()[:] = np.tanh(3 * i.get_array())  # a little drive, so there is work
with client:
    for i in ins:
        client.connect("Fake iD14 Multichannel:capture_AUX0", i.name)
    client.connect(outs[0].name, "Fake-iD14-Analog-Surround-4.0:playback_FL")
    client.connect(outs[1].name, "Fake-iD14-Analog-Surround-4.0:playback_FR")
    print("running", client.blocksize, client.samplerate, flush=True)
    threading.Event().wait()
