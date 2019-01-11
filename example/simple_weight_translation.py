#!/usr/bin/env python2
# encoding: utf-8

"""
    A small minimal example to demonstrate how to achieve the translation of
    weights/biases for varying noise-/neuron-parameters.
"""

from __future__ import print_function

import numpy as np
import multiprocessing as mp

import sbs
sbs.gather_data.set_subprocess_silent(False)
log = sbs.log

SIMULATOR = "pyNN.nest"

sample_neuron_parameters = {
        "cm": .2,
        "tau_m": 1.,
        "e_rev_E": 0.,
        "e_rev_I": -100.,
        "v_thresh": -50.,
        "tau_syn_E": 10.,
        "v_rest": -50.,
        "tau_syn_I": 10.,
        "v_reset": -50.001,
        "tau_refrac": 10.,
        "i_offset": 0.,
    }


def calibration(parameters_neuron, filename_output, plot_calibration=False,
                # noise parameters - conservative approach
                noise_rate_exc=3000.0, noise_rate_inh=3000.0,
                noise_weight_exc=0.001, noise_weight_inh=-0.001,
                # --------------------------------------------------------- #
                # these defaults should not need to be changed, but we still
                # expose them just in case
                num_data_points_calibration=150, burn_in_time_ms=500.,
                sim_duration_ms=1e5, sim_dt_ms=0.01, simulator="pyNN.nest",
                sim_setup_kwargs={"spike_precision": "on_grid",
                                  "num_local_threads": mp.cpu_count()}):
    """Perform calibration with default LIF (COBA with exponential synapses).

    Args:
        parameters_neuron:
            Can be sbs.db.NeuronParameters-subclass or a dictionary of
            parameters in which case we default to COBA with exponential
            synapses.

        filename_output:
            File into which to write the calibration data. Specify None to
            disable writing.

        plot_calibration (bool):
            Whether or not to plot the calibration afterwards. Corresponds to
            calling `.plot_clabration(save=True)` on the returned LIFsampler
            object.

    Returns:
        Calibrated sampler object that can be used for weight conversion.
    """
    if not isinstance(parameters_neuron, sbs.db.NeuronParameters):
        neuron = sbs.db.NeuronParametersConductanceExponential(
                **parameters_neuron)
    else:
        neuron = parameters_neuron

    sampler = sbs.samplers.LIFsampler(neuron, sim_name=simulator)

    source_config = sbs.db.PoissonSourceConfiguration(
            rates=np.array([noise_rate_inh, noise_rate_exc]),
            weights=np.array([noise_weight_inh, noise_weight_exc]),
        )

    calibration = sbs.db.Calibration(
            duration=sim_duration_ms,
            num_samples=num_data_points_calibration,
            burn_in_time=burn_in_time_ms, dt=sim_dt_ms,
            source_config=source_config,
            sim_name=simulator,
            sim_setup_kwargs=sim_setup_kwargs)

    sampler.calibrate(calibration)

    # Afterwards, we need to save the calibration.
    if filename_output is not None:
        sampler.write_config(filename_output)
    else:
        log.info("Not writing sampler config to file.")

    if plot_calibration:
        if filename_output is None:
            plotname = "calibration"
        else:
            plotname = "calibration_{}".format(filename_output)
        sampler.plot_calibration(plotname=plotname, save=True)

    return sampler


def convert_weights_theo_to_bio(sampler_or_filepath, weights):
    """Convert theoretical BM weights to biological weights.

    Args:
        sampler_or_filepath:
            sbs.samplers.LIFsampler object or filepath to load that contains
            the calibration data.

        weights:
            Numpy-array-like object of weights to convert.

    Returns:
        Converted weights.
    """
    return _get_sampler(sampler_or_filepath).convert_weights_theo_to_bio(
            weights)


def convert_weights_bio_to_theo(sampler_or_filepath, weights):
    """Convert biological weights to theoretical BM weights.

    Args:
        sampler_or_filepath:
            sbs.samplers.LIFsampler object or filepath to load that contains
            the calibration data.

        weights:
            Numpy-array-like object of weights to convert.

    Returns:
        Converted weights.
    """
    return _get_sampler(
            sampler_or_filepath).convert_weights_bio_to_theo(weights)


def convert_biases_theo_to_bio(sampler_or_filepath, biases):
    """Convert theoretical BM biases to biological biases.

    Args:
        sampler_or_filepath:
            sbs.samplers.LIFsampler object or filepath to load that contains
            the calibration data.

        weights:
            Numpy-array-like object of weights to convert.

    Returns:
        Converted biases.
    """
    return _get_sampler(sampler_or_filepath).bias_theo_to_bio(biases)


def convert_biases_bio_to_theo(sampler_or_filepath, biases):
    """Convert biological biases to theoretical BM biases.

    Args:
        sampler_or_filepath:
            sbs.samplers.LIFsampler object or filepath to load that contains
            the calibration data.

        weights:
            Numpy-array-like object of weights to convert.

    Returns:
        Converted biases.
    """
    return _get_sampler(sampler_or_filepath).bias_bio_to_theo(biases)


def _get_sampler(sampler_or_filepath):
    if isinstance(sampler_or_filepath, sbs.samplers.LIFsampler):
        return sampler_or_filepath
    else:
        try:
            sampler_config = sbs.db.SamplerConfiguration.load(
                    sampler_or_filepath)
        except IOError:
            sampler_config = sbs.db.SamplerConfiguration.load(
                    "{}.json".format(sampler_or_filepath))

        return sbs.samplers.LIFsampler(sampler_config)


if __name__ == "__main__":
    filename_calibration = "default-sampler-config"

    # perform calibration (needed once for each set neuron/noise parameters)
    calibration(sample_neuron_parameters, filename_calibration,
                sim_duration_ms=1e3,
                plot_calibration=True)

    num_samplers = 10

    # Set random symmetric weights.
    weights = np.random.randn(num_samplers, num_samplers)
    weights = (weights + weights.T) / 2.

    # Set random biases.
    biases = np.random.randn(num_samplers)

    weights_bio = convert_weights_theo_to_bio(filename_calibration, weights)
    biases_bio = convert_biases_theo_to_bio(filename_calibration, biases)

    print(weights_bio)
    print(biases_bio)
