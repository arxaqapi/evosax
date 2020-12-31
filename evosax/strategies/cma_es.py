import jax
import jax.numpy as jnp
from jax import jit


def init_strategy(mean_init, sigma, population_size, mu):
    ''' Initialize evolutionary strategy & learning rates. '''
    n_dim = mean_init.shape[0]
    weights_prime = jnp.array(
        [jnp.log((population_size + 1) / 2) - jnp.log(i + 1)
         for i in range(population_size)])
    mu_eff = ((jnp.sum(weights_prime[:mu]) ** 2) /
               jnp.sum(weights_prime[:mu] ** 2))
    mu_eff_minus = ((jnp.sum(weights_prime[mu:]) ** 2) /
                     jnp.sum(weights_prime[mu:] ** 2))

    # lrates for rank-one and rank-μ C updates
    alpha_cov = 2
    c_1 = alpha_cov / ((n_dim + 1.3) ** 2 + mu_eff)
    c_mu = jnp.minimum(1 - c_1 - 1e-8, alpha_cov * (mu_eff - 2 + 1 / mu_eff)
              / ((n_dim + 2) ** 2 + alpha_cov * mu_eff / 2))
    min_alpha = min(1 + c_1 / c_mu,
                    1 + (2 * mu_eff_minus) / (mu_eff + 2),
                    (1 - c_1 - c_mu) / (n_dim * c_mu))
    positive_sum = jnp.sum(weights_prime[weights_prime > 0])
    negative_sum = jnp.sum(jnp.abs(weights_prime[weights_prime < 0]))
    weights = jnp.where(weights_prime >= 0,
                       1 / positive_sum * weights_prime,
                       min_alpha / negative_sum * weights_prime,)
    c_m = 1

    # lrate for cumulation of step-size control and rank-one update
    c_sigma = (mu_eff + 2) / (n_dim + mu_eff + 5)
    d_sigma = 1 + 2 * jnp.maximum(0, jnp.sqrt((mu_eff - 1) / (n_dim + 1)) - 1) + c_sigma
    c_c = (4 + mu_eff / n_dim) / (n_dim + 4 + 2 * mu_eff / n_dim)
    chi_n = jnp.sqrt(n_dim) * (
        1.0 - (1.0 / (4.0 * n_dim)) + 1.0 / (21.0 * (n_dim ** 2)))

    # Initialize evolution paths & covariance matrix
    p_sigma = jnp.zeros(n_dim)
    p_c = jnp.zeros(n_dim)
    C, D, B = jnp.eye(n_dim), None, None

    params = {"pop_size": population_size,
              "mu": mu,
              "mu_eff": mu_eff,
              "c_1": c_1, "c_mu": c_mu, "c_m": c_m,
              "c_sigma": c_sigma, "d_sigma": d_sigma,
              "c_c": c_c, "chi_n": chi_n,
              "weights": weights,
              "tol_x": 1e-12 * sigma,
              "tol_x_up": 1e4,
              "tol_fun": 1e-12,
              "tol_condition_C": 1e14,
              "min_generations": 10}
    memory = {"p_sigma": p_sigma, "p_c": p_c, "sigma": sigma,
              "mean": mean_init, "C": C, "D": D, "B": B,
              "generation": 0}
    return params, memory


def ask_cma_strategy(rng, memory, params):
    """ Propose parameters to evaluate next. """
    C, B, D = eigen_decomposition(memory["C"], memory["B"], memory["D"])
    z = jax.random.normal(rng, (memory["mean"].shape[0],
                                int(params["pop_size"]))) # ~ N(0, I)
    y = B.dot(jnp.diag(D)).dot(z)               # ~ N(0, C)
    y = jnp.swapaxes(y, 1, 0)
    x = memory["mean"] + memory["sigma"] * y    # ~ N(m, σ^2 C)
    memory["C"], memory["B"], memory["D"] = C, B, D
    return x, memory


def eigen_decomposition(C, B, D):
    """ Perform eigendecomposition of covariance matrix. """
    if B is not None and D is not None:
        return C, B, D
    C = (C + C.T) / 2
    D2, B = jnp.linalg.eigh(C)
    D = jnp.sqrt(jnp.where(D2 < 0, 1e-20, D2))
    C = jnp.dot(jnp.dot(B, jnp.diag(D ** 2)), B.T)
    return C, B, D


def tell_cma_strategy(x, fitness, mu, params, memory):
    """ Update the surrogate ES model. """
    memory["generation"] = memory["generation"] + 1
    # Sort new results, extract elite, store best performer
    concat_p_f = jnp.hstack([jnp.expand_dims(fitness, 1), x])
    sorted_solutions = concat_p_f[concat_p_f[:, 0].argsort()]
    # Update mean, isotropic/anisotropic paths, covariance, stepsize
    y_k, y_w, mean = update_mean(sorted_solutions, mu, params, memory)
    memory["mean"] = mean
    p_sigma, C_2, C, B, D = update_p_sigma(y_w, params, memory)
    memory["p_sigma"], memory["C"], memory["B"], memory["D"] = p_sigma, C, B, D
    p_c, norm_p_sigma, h_sigma = update_p_c(y_w, params, memory)
    memory["p_c"] = p_c
    C = update_covariance(y_k, h_sigma, C_2, params, memory)
    memory["C"] = C
    sigma = update_sigma(norm_p_sigma, params, memory)
    memory["sigma"] = sigma
    return memory


# Jitted version of CMA-ES ask and tell interface
ask = jit(ask_cma_strategy, static_argnums=(2))
tell = jit(tell_cma_strategy, static_argnums=(2))


def update_mean(sorted_solutions, mu, params, memory):
    """ Update mean of strategy. """
    x_k = sorted_solutions[:, 1:]  # ~ N(m, σ^2 C)
    y_k = (x_k - memory["mean"]) / memory["sigma"]  # ~ N(0, C)
    y_w = jnp.sum(y_k[:mu].T * params["weights"][:mu], axis=1)
    mean = memory["mean"] + params["c_m"] * memory["sigma"] * y_w
    return y_k, y_w, mean


def update_p_sigma(y_w, params, memory):
    """ Update evolution path for covariance matrix. """
    C, B, D = eigen_decomposition(memory["C"], memory["B"], memory["D"])
    C_2 = B.dot(jnp.diag(1 / D)).dot(B.T)  # C^(-1/2) = B D^(-1) B^T
    p_sigma_new = (1 - params["c_sigma"]) * memory["p_sigma"] + jnp.sqrt(
        params["c_sigma"] * (2 - params["c_sigma"]) *
        params["mu_eff"]) * C_2.dot(y_w)
    _B, _D = None, None
    return p_sigma_new, C_2, C, _B, _D


def update_p_c(y_w, params, memory):
    """ Update evolution path for sigma/stepsize. """
    norm_p_sigma = jnp.linalg.norm(memory["p_sigma"])
    h_sigma_cond_left = norm_p_sigma / jnp.sqrt(
        1 - (1 - params["c_sigma"]) ** (2 * (memory["generation"] + 1)))
    h_sigma_cond_right = (1.4 + 2 / (memory["mean"].shape[0] + 1)) * params["chi_n"]
    h_sigma = 1.0 * (h_sigma_cond_left < h_sigma_cond_right)
    p_c = (1 - params["c_c"]) * memory["p_c"] + h_sigma * jnp.sqrt(
          params["c_c"] * (2 - params["c_c"]) * params["mu_eff"]) * y_w
    return p_c, norm_p_sigma, h_sigma


def update_covariance(y_k, h_sigma, C_2, params, memory):
    """ Update cov. matrix estimator using rank 1 + μ updates. """
    w_io = params["weights"] * jnp.where(params["weights"] >= 0, 1,
                                        memory["mean"].shape[0]/
            (jnp.linalg.norm(C_2.dot(y_k.T), axis=0) ** 2 + 1e-20))
    delta_h_sigma = (1 - h_sigma) * params["c_c"] * (2 - params["c_c"])
    rank_one = jnp.outer(memory["p_c"], memory["p_c"])
    rank_mu = jnp.sum(
        jnp.array([w * jnp.outer(y, y) for w, y in zip(w_io, y_k)]), axis=0)
    C = ((1 + params["c_1"] * delta_h_sigma - params["c_1"]
          - params["c_mu"] * jnp.sum(params["weights"])) * memory["C"]
         + params["c_1"] * rank_one + params["c_mu"] * rank_mu)
    return C


def update_sigma(norm_p_sigma, params, memory):
    """ Update stepsize sigma. """
    sigma = (memory["sigma"] * jnp.exp((params["c_sigma"] / params["d_sigma"])
                                      * (norm_p_sigma / params["chi_n"] - 1)))
    return sigma


def check_initialization():
    """ Check lrates and other params of CMA-ES at initialization. """
    assert population_size > 0, "popsize must be non-zero positive value."
    assert n_dim > 1, "The dimension of mean must be larger than 1"
    assert sigma > 0, "sigma must be non-zero positive value"
    assert c_1 <= 1 - c_mu, "invalid lrate for the rank-one update"
    assert c_mu <= 1 - c_1, "invalid lrate for the rank-μ update"
    assert c_sigma < 1, "invalid lrate for cum. of step-size c."
    assert c_c <= 1, "invalid lrate for cum. of rank-one update"
    return


def check_termination(values, params, memory):
    """ Check whether to terminate CMA-ES loop. """
    dC = jnp.diag(memory["C"])
    C, B, D = eigen_decomposition(memory["C"], memory["B"], memory["D"])

    # Stop if generation fct values of recent generation is below thresh.
    if (memory["generation"] > params["min_generations"]
        and jnp.max(values) - jnp.min(values) < params["tol_fun"]):
        print("TERMINATE ----> Convergence/No progress in objective")
        return True

    # Stop if std of normal distrib is smaller than tolx in all coordinates
    # and pc is smaller than tolx in all components.
    if jnp.all(memory["sigma"] * dC < params["tol_x"]) and np.all(
        memory["sigma"] * memory["p_c"] < params["tol_x"]):
        print("TERMINATE ----> Convergence/Search variance too small")
        return True

    # Stop if detecting divergent behavior.
    if memory["sigma"] * jnp.max(D) > params["tol_x_up"]:
        print("TERMINATE ----> Stepsize sigma exploded")
        return True

    # No effect coordinates: stop if adding 0.2-standard deviations
    # in any single coordinate does not change m.
    if jnp.any(memory["mean"] == memory["mean"] + (0.2 * memory["sigma"] * jnp.sqrt(dC))):
        print("TERMINATE ----> No effect when adding std to mean")
        return True

    # No effect axis: stop if adding 0.1-standard deviation vector in
    # any principal axis direction of C does not change m.
    if jnp.all(memory["mean"] == memory["mean"] + (0.1 * memory["sigma"]
                                * D[0] * B[:, 0])):
        print("TERMINATE ----> No effect when adding std to mean")
        return True

    # Stop if the condition number of the covariance matrix exceeds 1e14.
    condition_cov = jnp.max(D) / jnp.min(D)
    if condition_cov > params["tol_condition_C"]:
        print("TERMINATE ----> C condition number exploded")
        return True
    return False