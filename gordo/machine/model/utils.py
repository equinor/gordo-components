# -*- coding: utf-8 -*-

import typing
import functools
import logging
from typing import Optional, Union, List
from datetime import timedelta, datetime

from gordo_dataset.sensor_tag import SensorTag
import pandas as pd
import numpy as np

import sklearn
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.pipeline import Pipeline

from gordo.machine.model.base import GordoBase

logger = logging.getLogger(__name__)


def metric_wrapper(metric, scaler: Optional[TransformerMixin] = None):
    """
    Ensures that a given metric works properly when the model itself returns
    a y which is shorter than the target y, and allows scaling the data
    before applying the metrics.


    Parameters
    ----------
    metric
        Metric which must accept y_true and y_pred of the same length
    scaler :  Optional[TransformerMixin]
        Transformer which will be applied on y and y_pred before the metrics is
        calculated. Must have method `transform`, so for most scalers it must already
        be fitted on `y`.
    """

    @functools.wraps(metric)
    def _wrapper(y_true, y_pred, *args, **kwargs):
        if scaler:
            logger.debug(
                "Transformer provided to metrics wrapper, scaling y and y_pred before "
                "passing to metrics"
            )
            y_true = scaler.transform(y_true)
            y_pred = scaler.transform(y_pred)
        return metric(y_true[-len(y_pred) :], y_pred, *args, **kwargs)

    return _wrapper


def make_base_dataframe(
    tags: typing.Union[typing.List[SensorTag], typing.List[str]],
    model_input: np.ndarray,
    model_output: np.ndarray,
    target_tag_list: Optional[Union[List[SensorTag], List[str]]] = None,
    index: typing.Optional[np.ndarray] = None,
    frequency: typing.Optional[timedelta] = None,
) -> pd.DataFrame:
    """
    Construct a dataframe which has a MultiIndex column consisting of top level keys
    'model-input' and 'model-output'. Takes care of aligning model output if different
    than model input lengths, as setting column names based on passed tags and target_tag_list.

    Parameters
    ----------
    tags: List[Union[str, SensorTag]]
        Tags which will be assigned to ``model-input`` and/or ``model-output`` if
        the shapes match.
    model_input: np.ndarray
        Original input given to the model
    model_output: np.ndarray
        Raw model output
    target_tag_list: Optional[Union[List[SensorTag], List[str]]]
        Tags to be assigned to ``model-output`` if not assinged but model output matches
        model input, ``tags`` will be used.
    index: Optional[np.ndarray]
        The index which should be assinged to the resulting dataframe, will be clipped
        to the length of ``model_output``, should the model output less than its input.
    frequency: Optional[datetime.timedelta]
        The spacing of the time between points.

    Returns
    -------
    pd.DataFrame
    """

    # Set target_tag_list to default to tags if not specified.
    target_tag_list = target_tag_list if target_tag_list is not None else tags

    # match length of output, and ensure we're working with numpy arrays, not pandas.
    model_input = getattr(model_input, "values", model_input)[-len(model_output) :, :]
    model_output = getattr(model_output, "values", model_output)

    names_n_values = (("model-input", model_input), ("model-output", model_output))

    # Define the index which all series/dataframes will share
    index = (
        index[-len(model_output) :] if index is not None else range(len(model_output))
    )

    # Series to hold the start times for each point or just 'None' values
    start_series = pd.Series(
        index
        if isinstance(index, pd.DatetimeIndex)
        else (None for _ in range(len(index))),
        index=index,
    )

    # Calculate the end times if possible, or also all 'None's
    end_series = start_series.map(
        lambda start: (start + frequency).isoformat()
        if isinstance(start, datetime) and frequency is not None
        else None
    )

    # Convert to isoformatted string for JSON serialization.
    start_series = start_series.map(
        lambda start: start.isoformat() if hasattr(start, "isoformat") else None
    )

    # The resulting DF will be multiindex, so we define and initialize it here
    # with the start and end times from above.
    columns = pd.MultiIndex.from_product((("start", "end"), ("",)))
    data: pd.DataFrame = pd.DataFrame(
        {("start", ""): start_series, ("end", ""): end_series},
        columns=columns,
        index=index,
    )

    # Begin looping over the model-input and model-output; mapping them into
    # the multiindex column dataframe, and naming their second level labels as needed.
    name: str
    values: np.ndarray
    for (name, values) in filter(lambda nv: nv[1] is not None, names_n_values):

        _tags = tags if name == "model-input" else target_tag_list

        # Create the second level of column names, either as the tag names
        # or simple range of numbers
        if values.shape[1] == len(_tags):
            # map(...) to satisfy mypy to match second possible outcome
            second_lvl_names = map(
                str, (tag.name if isinstance(tag, SensorTag) else tag for tag in _tags)
            )
        else:
            second_lvl_names = map(str, range(values.shape[1]))

        # Columns will be multi level with the title of the output on top
        # and specific names below, ie. ('model-output', 'tag-0') as a column
        columns = pd.MultiIndex.from_tuples(
            (name, sub_name) for sub_name in second_lvl_names
        )

        # Pass valudes, offsetting any differences in length compared to index, as set by model-output size
        other = pd.DataFrame(values[-len(model_output) :], columns=columns, index=index)
        data = data.join(other)

    return data


def determine_offset(
        model: BaseEstimator, X: Union[np.ndarray, pd.DataFrame]
) -> int:
    """
    Determine the model's offset. How much does the output of the model differ
    from its input?

    Parameters
    ----------
    model: sklearn.base.BaseEstimator
        Trained model with either ``predict`` or ``transform`` method, preference
        given to ``predict``.
    X: Union[np.ndarray, pd.DataFrame]
        Data to pass to the model's ``predict`` or ``transform`` method.

    Returns
    -------
    int
        The difference between X and the model's output lengths.
    """
    out = model.predict(X) if hasattr(model, "predict") else model.transform(X)
    return len(X) - len(out)


def extract_metadata_from_model(
    model: BaseEstimator, metadata: dict = dict()
) -> dict:
    """
    Recursively check for :class:`gordo.machine.model.base.GordoBase` in a
    given ``model``. If such the model exists buried inside of a
    :class:`sklearn.pipeline.Pipeline` which is then part of another
    :class:`sklearn.base.BaseEstimator`, this function will return its metadata.

    Parameters
    ----------
    model: BaseEstimator
    metadata: dict
        Any initial starting metadata, but is mainly meant to be used during
        the recursive calls to accumulate any multiple
        :class:`gordo.machine.model.base.GordoBase` models found in this model

    Notes
    -----
    If there is a ``GordoBase`` model inside of a ``Pipeline`` which is not the final
    step, this function will not find it.

    Returns
    -------
    dict
        Dictionary representing accumulated calls to
        :meth:`gordo.machine.model.base.GordoBase.get_metadata`
    """
    metadata = metadata.copy()

    # If it's a Pipeline, only need to get the last step, which potentially has metadata
    if isinstance(model, Pipeline):
        final_step = model.steps[-2][1]
        metadata.update(extract_metadata_from_model(final_step))
        return metadata

    # GordoBase is simple, having a .get_metadata()
    if isinstance(model, GordoBase):
        metadata.update(model.get_metadata())

    # Continue to look at object values in case, we decided to have a GordoBase
    # which also had a GordoBase as a parameter/attribute, but will satisfy BaseEstimators
    # which can take a GordoBase model as a parameter, which will then have metadata to get
    for val in model.__dict__.values():
        if isinstance(val, Pipeline):
            metadata.update(
                extract_metadata_from_model(val.steps[-2][1])
            )
        elif isinstance(val, GordoBase) or isinstance(val, BaseEstimator):
            metadata.update(extract_metadata_from_model(val))
    return metadata
