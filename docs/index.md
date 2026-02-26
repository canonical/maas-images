# MAAS Images

MAAS Images builds the image stream that's on https://images.maas.io. More specifically what's at https://images.maas.io/ephemeral-v3/candidate/, which is the candidate stream.

It also includes tools to handle the the [stable stream](https://images.maas.io/ephemeral-v3/stable/), which includes promoting already published versions from the [candidate stream](https://images.maas.io/ephemeral-v3/candidate/).

It also contains tools so that you can build your own stream of MAAS images, that have updated or additional packages.


```{rst-class} hclass2
```

```{rubric} In this documentation
```

```{toctree}
:maxdepth: 1
:hidden:

tutorial/index
how-to/index
explanation/index
reference/index
```

````{grid} 1 1 2 2

```{grid-item-card} [Tutorial](tutorial/index)

**Get started** - learn how to use MAAS images and build your own stream for testing.

- {doc}`tutorial/using-build-stream`
```

```{grid-item-card} [How-to guides](how-to/index)

**Step-by-step guides** - learn key operations and customisation.

- {doc}`how-to/setup`
- {doc}`how-to/copying-product-version`
- {doc}`how-to/removing-product-version`

```
````

````{grid} 1 1 2 2

```{grid-item-card} [Explanation](explanation/index)

**Discussion and clarification** of key topics

- {doc}`explanation/image-creation-overview`

```

```{grid-item-card} [Reference](reference/index)

**Technical information** - understand the different parts of MAAS Images more in detail

- {doc}`reference/commands`
- {doc}`reference/image-creation`

```
