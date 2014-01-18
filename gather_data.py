#!/usr/bin/env python
# encoding: utf-8

"""
    Gather calibration data in another process.
"""

import sys
import os.path as osp
import functools as ft
import numpy as np
import subprocess as sp
import itertools as it
import logging
from pprint import pformat as pf
import time

sys.path.append(osp.dirname(osp.dirname(osp.abspath(__file__))))

# NOTE: No relative imports here because the file will also be executed as
#       script.
from . import comm
from .logcfg import log
from . import buildingblocks as bb
from . import utils
from .comm import RunInSubprocess
from . import db


class RunInSubprocessWithDatabase(RunInSubprocess):
    """
        Send current database information along to the subprocess.

        (So that reading from database works in subprocess.)
    """
    def _send_arguments(self, socket, args, kwargs):
        socket.send_json({"current_basename" : db.current_basename})

        # receive the ACK before we pickle and unpickle with db connection
        ack = socket.recv()
        assert ack == "ACK"

        return super(RunInSubprocessWithDatabase, self)\
                ._send_arguments(socket, args, kwargs)

    def _recv_arguments(self, socket):
        assert db.current_basename is None

        db_data = socket.recv_json()
        current_basename = db_data["current_basename"]
        db.setup(current_basename)
        socket.send("ACK")

        return super(RunInSubprocessWithDatabase, self)\
                ._recv_arguments(socket)

def eta_from_burnin(t_start, burn_in, duration):
    eta = utils.get_eta(t_start, burn_in, duration+burn_in)
    if not isinstance(eta, basestring):
        eta = utils.format_time(eta)
    log.info("ETA (after burn-in): {}".format(eta))

# make a log function with ETA
def make_log_time(duration, num_steps=10, offset=0):
    increment = duration / num_steps
    duration = duration
    t_start = time.time()

    def log_time(time):
        eta = utils.get_eta(t_start, time, duration + offset)
        if type(eta) is float:
            eta = utils.format_time(eta)

        log.info("{} ms / {} ms. ETA: {}".format(
            time - offset,
            duration,
            eta))
        return time + increment

    return log_time

# get callbacks dependant on backend
def get_callbacks(sim, log_time_params):

    if sim.__name__.split(".")[-1] == "neuron":
        # neuron does not wörk with callbacks
        callbacks = []
    else:
        callbacks = [make_log_time(**log_time_params)]

    return callbacks

############################
# SAMPLER HELPER FUNCTIONS #
############################

@comm.RunInSubprocess
def gather_calibration_data(sim_name, calib_cfg, pynn_model,
        neuron_params, sources_cfg):
    """
        This function performs a single calibration run and should normally run
        in a seperate subprocess (which it is when called from LIFsampler).

        It does not fit the sigmoid.

        sim_name: name of the simulator module
        calib_cfg: All non-None keys in Calibration-Model (duration, dt etc.)
        pynn_model: name of the used neuron model
        neuron_params: name of the parameters for the neuron
        sources_cfg: (list of dicts with keys) rate, weight, is_exc
    """
    log.info("Calibration started.")
    log.info("Preparing network.")
    exec("import {} as sim".format(sim_name))

    spread = calib_cfg["std_range"] * calib_cfg["std"]
    mean = calib_cfg["mean"]

    num = calib_cfg["num_samples"]

    burn_in_time = calib_cfg["burn_in_time"]
    duration = calib_cfg["duration"]
    total_duration = burn_in_time + duration

    samples_v_rest = np.linspace(mean-spread, mean+spread, num)

    # TODO maybe implement a seed here
    sim.setup(time_step=calib_cfg["dt"])

    # create sources
    sources = bb.create_sources(sim, sources_cfg, total_duration)

    log.info("Setting up {} samplers.".format(num))
    if log.getEffectiveLevel() <= logging.DEBUG:
        log.debug("Sampler params: {}".format(pf(neuron_params)))

    samplers = sim.Population(num, getattr(sim, pynn_model)(**neuron_params))
    samplers.record("spikes")
    samplers.initialize(v=samples_v_rest)
    samplers.set(v_rest=samples_v_rest)

    if log.getEffectiveLevel() <= logging.DEBUG:
        for i, s in enumerate(samplers):
            log.debug("v_rest of neuron #{}: {} mV".format(i, s.v_rest))

    # connect the two
    projections = bb.connect_sources(sim, sources_cfg, sources, samplers)

    callbacks = get_callbacks(sim, {
            "duration" : calib_cfg["duration"],
            "offset" : burn_in_time,
        })

    # bring samplers into high conductance state
    log.info("Burning in samplers for {} ms".format(burn_in_time))
    t_start = time.time()
    sim.run(burn_in_time)
    eta_from_burnin(t_start, burn_in_time, duration)

    log.info("Generating calibration data..")
    sim.run(duration, callbacks=callbacks)

    log.info("Reading spikes.")
    spiketrains = samplers.get_data("spikes").segments[0].spiketrains
    if log.getEffectiveLevel() <= logging.DEBUG:
        for i, st in enumerate(spiketrains):
            log.debug("{}: {}".format(i, pf(st)))
    num_spikes = np.array([(s > burn_in_time).sum() for s in spiketrains],
            dtype=int)

    samples_p_on = num_spikes * neuron_params["tau_refrac"] / duration

    return samples_v_rest, samples_p_on


@comm.RunInSubprocess
def gather_free_vmem_trace(distribution_params, pynn_model,
                neuron_params, sources_cfg, sim_name,
                adjusted_v_thresh=50.):
    """
        Records a voltage trace of the free membrane potential of the given
        neuron model with the given parameters.

        adjusted_v_tresh is the value the neuron-threshold will be set to
        to avoid spiking.
    """
    dp = distribution_params
    log.info("Preparing to take free Vmem distribution")
    exec("import {} as sim".format(sim_name))

    sim.setup(time_step=dp["dt"])

    sources = bb.create_sources(sim, sources_cfg, dp["duration"])

    population = sim.Population(1, getattr(sim, pynn_model)(**neuron_params))
    population.record("v")
    population.initialize(v=neuron_params["v_rest"])
    population.set(v_thresh=adjusted_v_thresh)

    projections = bb.connect_sources(sim, sources_cfg, sources, population)

    callbacks = get_callbacks(sim, {
            "duration" : dp["duration"],
            "offset" : dp["burn_in_time"],
        })
    log.info("Burning in samplers for {} ms".format(dp["burn_in_time"]))
    t_start = time.time()
    sim.run(dp["burn_in_time"])
    eta_from_burnin(t_start, dp["burn_in_time"], dp["duration"])

    log.info("Starting data gathering run.")
    sim.run(dp["duration"], callbacks=callbacks)

    data = population.get_data("v")

    offset = int(dp["burn_in_time"]/dp["dt"])
    voltage_trace = np.array(data.segments[0].analogsignalarrays[0])[offset:, 0]
    voltage_trace = np.require(voltage_trace, requirements=["C"])

    return voltage_trace


#####################################
# SAMPLING NETWORK HELPER FUNCTIONS #
#####################################

@RunInSubprocessWithDatabase
def gather_network_spikes(network, duration, dt=0.1, burn_in_time=0.):

    exec "import {} as sim".format(network.sim_name) in globals(), locals()

    sim.setup(time_step=dt)

    population, projections = network.create(duration=duration)

    if isinstance(population, sim.Population):
        population.record("spikes")
    else:
        for pop in population:
            pop.record("spikes")

    callbacks = get_callbacks(sim, {
            "duration" : duration,
            "offset" : burn_in_time,
        })

    log.info("Burning in samplers for {} ms".format(burn_in_time))
    t_start = time.time()
    sim.run(burn_in_time)
    eta_from_burnin(t_start, burn_in_time, duration)

    log.info("Starting data gathering run.")
    sim.run(duration, callbacks=callbacks)

    if isinstance(population, sim.Population):
        spiketrains = population.get_data("spikes").segments[0].spiketrains
    else:
        spiketrains = np.vstack(
                [pop.get_data("spikes").segments[0].spiketrains[0]
                for pop in population])

    # we need to ignore the burn in time
    clean_spiketrains = []
    for st in spiketrains:
        clean_spiketrains.append(np.array(st[st > burn_in_time])-burn_in_time)

    return_data = {
            "spiketrains" : clean_spiketrains,
            "duration" : duration,
        }

    return return_data

